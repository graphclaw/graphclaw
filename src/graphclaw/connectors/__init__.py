"""graphclaw.connectors — External service connector framework.

Description
-----------
Provides a unified plugin architecture for connecting GraphClaw to external
services.  All connectors implement ``ConnectorABC`` and follow the same
lifecycle pattern (connect → use → disconnect) via async context managers.

Built-in connector types
------------------------
- ``google_calendar``: Google Calendar via google-api-python-client.
- ``outlook_calendar``: Outlook/Exchange via Microsoft Graph API (httpx).
- ``jira``: Jira Cloud/Server issue import via httpx.
- ``asana``: Asana task import via httpx.
- ``notion``: Notion database import via httpx.

Usage
-----
::

    from graphclaw.connectors import create_connector, ConnectorConfig

    config = ConnectorConfig(
        connector_type="jira",
        credentials={"server_url": "...", "username": "...", "api_token": "..."},
    )
    async with create_connector("jira", config) as conn:
        projects = await conn.list_projects()

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""
from __future__ import annotations

from graphclaw.connectors.base import ConnectorABC, ConnectorConfig
from graphclaw.connectors.calendar.base import CalendarConnector
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot
from graphclaw.connectors.factory import create_connector
from graphclaw.connectors.import_.base import ImportConnector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem
from graphclaw.connectors.registry import ConnectorRegistry, default_registry

__all__ = [
    # Core abstractions
    "ConnectorABC",
    "ConnectorConfig",
    "ConnectorRegistry",
    # Factory
    "create_connector",
    # Default registry
    "default_registry",
    # Calendar
    "CalendarConnector",
    "CalendarEvent",
    "FreeBusySlot",
    # Import
    "ImportConnector",
    "ImportItem",
    "ImportBatch",
]
