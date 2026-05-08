# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.connectors.registry — Connector type registry.

Description
-----------
Provides ``ConnectorRegistry``, a simple dict-backed registry that maps
connector type strings to their concrete ``ConnectorABC`` subclasses.  The
registry is pre-populated at import time with all built-in connector types.

Design Patterns
---------------
- Registry: Central lookup table indexed by ``connector_type`` string.
- Lazy Registration: Each concrete adapter module registers itself when first
  imported; the registry is populated by ``registry.py`` at package load.

Public API
----------
- ConnectorRegistry: list_types(), get(connector_type) -> type[ConnectorABC].

Dependencies
------------
- graphclaw.connectors.base: ConnectorABC.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import logging

from graphclaw.connectors.base import ConnectorABC

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Registry mapping connector type strings to connector classes.

    The registry is populated at module load time with all built-in
    connector types.  Additional types can be registered at runtime
    via ``register()``.

    Example
    -------
    ::

        from graphclaw.connectors.registry import ConnectorRegistry
        registry = ConnectorRegistry()
        cls = registry.get("google_calendar")
    """

    def __init__(self) -> None:
        self._registry: dict[str, type[ConnectorABC]] = {}

    def register(self, cls: type[ConnectorABC]) -> None:
        """Register a connector class under its ``connector_type`` key.

        Args:
            cls: A concrete ``ConnectorABC`` subclass with a ``connector_type``
                 class variable set.

        Raises:
            AttributeError: If ``cls`` does not define ``connector_type``.
        """
        ctype = cls.connector_type
        self._registry[ctype] = cls
        logger.debug("Registered connector type: %s -> %s", ctype, cls.__name__)

    def get(self, connector_type: str) -> type[ConnectorABC]:
        """Return the class registered for *connector_type*.

        Args:
            connector_type: The connector type string (e.g. ``"jira"``).

        Returns:
            The concrete ``ConnectorABC`` subclass.

        Raises:
            ValueError: If *connector_type* is not registered.
        """
        cls = self._registry.get(connector_type)
        if cls is None:
            available = ", ".join(sorted(self._registry.keys()))
            raise ValueError(
                f"Unknown connector type {connector_type!r}. Available types: {available}"
            )
        return cls

    def list_types(self) -> list[str]:
        """Return a sorted list of all registered connector type strings."""
        return sorted(self._registry.keys())


def _build_default_registry() -> ConnectorRegistry:
    """Build and return the default registry with all built-in connectors."""
    registry = ConnectorRegistry()

    # Calendar connectors
    try:
        from graphclaw.connectors.calendar.google.adapter import (
            GoogleCalendarConnector,  # noqa: PLC0415
        )

        registry.register(GoogleCalendarConnector)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load GoogleCalendarConnector: %s", exc)

    try:
        from graphclaw.connectors.calendar.outlook.adapter import (
            OutlookCalendarConnector,  # noqa: PLC0415
        )

        registry.register(OutlookCalendarConnector)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load OutlookCalendarConnector: %s", exc)

    # Import connectors
    try:
        from graphclaw.connectors.import_.jira.adapter import JiraImportConnector  # noqa: PLC0415

        registry.register(JiraImportConnector)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load JiraImportConnector: %s", exc)

    try:
        from graphclaw.connectors.import_.asana.adapter import AsanaImportConnector  # noqa: PLC0415

        registry.register(AsanaImportConnector)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load AsanaImportConnector: %s", exc)

    try:
        from graphclaw.connectors.import_.notion.adapter import (
            NotionImportConnector,  # noqa: PLC0415
        )

        registry.register(NotionImportConnector)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load NotionImportConnector: %s", exc)

    return registry


# Module-level default registry, built at import time.
default_registry: ConnectorRegistry = _build_default_registry()


__all__ = [
    "ConnectorRegistry",
    "default_registry",
]
