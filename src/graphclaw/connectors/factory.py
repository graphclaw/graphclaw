# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.connectors.factory — Factory function for creating connector instances.

Description
-----------
Provides ``create_connector``, the single public entry point for instantiating
any connector from its type string and a ``ConnectorConfig``.  Concrete adapter
classes are lazy-imported from their subfolders so that optional third-party
dependencies (e.g. ``jira``, ``google-api-python-client``) are only required
when the corresponding connector type is actually used.

Design Patterns
---------------
- Factory Function: Encapsulates the mapping from type string → class → instance.
- Lazy Import: Each branch is only executed when the relevant type is requested,
  keeping startup time fast and avoiding hard dependencies on optional packages.

Public API
----------
- create_connector(connector_type, config) -> ConnectorABC.

Dependencies
------------
- graphclaw.connectors.base: ConnectorABC, ConnectorConfig.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from graphclaw.connectors.base import ConnectorABC, ConnectorConfig

# Mapping from connector_type string to (module path, class name) for lazy imports.
_CONNECTOR_MAP: dict[str, tuple[str, str]] = {
    "google_calendar": (
        "graphclaw.connectors.calendar.google.adapter",
        "GoogleCalendarConnector",
    ),
    "outlook_calendar": (
        "graphclaw.connectors.calendar.outlook.adapter",
        "OutlookCalendarConnector",
    ),
    "jira": (
        "graphclaw.connectors.import_.jira.adapter",
        "JiraImportConnector",
    ),
    "asana": (
        "graphclaw.connectors.import_.asana.adapter",
        "AsanaImportConnector",
    ),
    "notion": (
        "graphclaw.connectors.import_.notion.adapter",
        "NotionImportConnector",
    ),
}


def create_connector(connector_type: str, config: ConnectorConfig) -> ConnectorABC:
    """Instantiate and return a connector for the given *connector_type*.

    The concrete adapter class is lazy-imported from its subpackage to avoid
    pulling in optional third-party dependencies (e.g. ``google-api-python-client``,
    ``jira``) when they are not needed.

    Args:
        connector_type: The type identifier for the desired connector.
            Must be one of: ``google_calendar``, ``outlook_calendar``,
            ``jira``, ``asana``, ``notion``.
        config: A ``ConnectorConfig`` containing credentials and options.
            The ``connector_type`` field of *config* should match
            *connector_type*.

    Returns:
        An uninitialised ``ConnectorABC`` instance.  Call ``await conn.connect()``
        (or use as an async context manager) before making API calls.

    Raises:
        ValueError: If *connector_type* is not a known connector type.
        ImportError: If the connector's optional dependencies are not installed.

    Example
    -------
    ::

        config = ConnectorConfig(
            connector_type="jira",
            credentials={"server_url": "...", "username": "...", "api_token": "..."},
        )
        async with create_connector("jira", config) as conn:
            projects = await conn.list_projects()
    """
    entry = _CONNECTOR_MAP.get(connector_type)
    if entry is None:
        available = ", ".join(sorted(_CONNECTOR_MAP.keys()))
        raise ValueError(f"Unknown connector type {connector_type!r}. Available types: {available}")

    module_path, class_name = entry
    import importlib  # noqa: PLC0415

    module = importlib.import_module(module_path)
    cls: type[ConnectorABC] = getattr(module, class_name)
    return cls(config)


__all__ = [
    "create_connector",
]
