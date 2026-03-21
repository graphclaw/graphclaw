"""graphclaw.connectors.base — Abstract base class for all connector adapters.

Description
-----------
Defines ``ConnectorConfig`` (frozen dataclass for connection parameters) and
``ConnectorABC`` (abstract base class) that every external service connector
must implement.  Connectors provide a unified lifecycle interface (connect /
disconnect / health_check) and work as async context managers.

Design Patterns
---------------
- Abstract Base Class: ``ConnectorABC`` defines the minimal contract.
- Strategy: Different connector implementations are interchangeable at runtime.
- Context Manager: ``__aenter__`` / ``__aexit__`` manage lifecycle automatically.

Public API
----------
- ConnectorConfig: Frozen dataclass holding connector type, credentials, options.
- ConnectorABC: ABC with connect, disconnect, health_check, and context-manager support.

Dependencies
------------
- abc: ABC, abstractmethod.
- dataclasses: dataclass, field.
- typing: ClassVar.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ConnectorConfig:
    """Immutable configuration for a connector instance.

    Attributes
    ----------
    connector_type:
        The unique type identifier for this connector (e.g. ``"google_calendar"``).
    credentials:
        Mapping of credential key/value pairs (e.g. API tokens, OAuth secrets).
    options:
        Optional extra configuration (e.g. timeouts, base URLs, feature flags).
    """

    connector_type: str
    credentials: dict
    options: dict = field(default_factory=dict)


class ConnectorABC(ABC):
    """Abstract interface for all external service connectors.

    Every concrete connector must declare a ``connector_type`` class variable
    (used by the registry and factory) and implement the three abstract methods.

    Usage
    -----
    Use as an async context manager::

        async with GoogleCalendarConnector(config) as conn:
            events = await conn.list_events(since, until)

    Or manage lifecycle manually::

        conn = GoogleCalendarConnector(config)
        await conn.connect()
        try:
            events = await conn.list_events(since, until)
        finally:
            await conn.disconnect()
    """

    connector_type: ClassVar[str]

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection to the external service.

        Called automatically by ``__aenter__``.  Implementations should
        initialise HTTP clients, authenticate, and verify connectivity.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the connection to the external service.

        Called automatically by ``__aexit__``.  Implementations should
        close HTTP clients and release any held resources.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the connector can reach the external service.

        Should make a lightweight API call (e.g. list-projects, get-profile)
        and return ``False`` on any network or authentication error rather
        than raising.
        """

    async def __aenter__(self) -> ConnectorABC:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = [
    "ConnectorConfig",
    "ConnectorABC",
]
