"""graphclaw.connectors.import_.base — Abstract base class for import connectors.

Description
-----------
Defines ``ImportConnector``, the abstract intermediate class between
``ConnectorABC`` and the concrete Jira / Asana / Notion adapters.  It adds
the import-specific methods: listing available projects, fetching paginated
batches of items, and converting items into TaskNode-compatible dicts.

Design Patterns
---------------
- Abstract Base Class: extends ``ConnectorABC`` with domain-specific contract.
- Template Method: concrete adapters implement only the abstract methods; the
  context-manager lifecycle is inherited from ``ConnectorABC``.

Public API
----------
- ImportConnector: ABC with list_projects, fetch_items, to_task_nodes.

Dependencies
------------
- graphclaw.connectors.base: ConnectorABC.
- graphclaw.connectors.import_.models: ImportItem, ImportBatch.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from graphclaw.connectors.base import ConnectorABC
from graphclaw.connectors.import_.models import ImportBatch, ImportItem


class ImportConnector(ConnectorABC):
    """Abstract import connector for pulling tasks/issues from external systems.

    Concrete subclasses (``JiraImportConnector``, ``AsanaImportConnector``,
    ``NotionImportConnector``) implement these methods using their respective
    APIs.  All methods are async and must be called within an active connection
    context (i.e. after ``connect()`` has been awaited).
    """

    @abstractmethod
    async def list_projects(self) -> list[dict]:
        """Return a list of available projects in the source system.

        Returns:
            A list of dicts, each containing at minimum:

            - ``id`` (str): The project identifier (used in ``fetch_items``).
            - ``name`` (str): Human-readable project name.
            - ``description`` (str): Optional project description (may be empty).
        """

    @abstractmethod
    async def fetch_items(
        self,
        project_id: str,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ImportBatch:
        """Fetch a paginated batch of items from the given project.

        Args:
            project_id: The project/board/database identifier.
            since: Optional lower-bound datetime filter (items updated after
                this timestamp only).
            cursor: Opaque pagination cursor from a previous ``ImportBatch``
                (``None`` to start from the first page).
            limit: Maximum number of items to return per page.

        Returns:
            An ``ImportBatch`` containing the items, pagination cursor, and
            ``has_more`` flag.
        """

    @abstractmethod
    async def to_task_nodes(self, items: list[ImportItem]) -> list[dict]:
        """Convert a list of ``ImportItem`` instances to TaskNode creation dicts.

        Each returned dict should contain the fields expected by the TaskNode
        Pydantic model / graph store, at minimum:

        - ``title`` (str)
        - ``description`` (str)
        - ``task_type`` (str)
        - ``status`` (str)
        - ``due_date`` (datetime | None)

        Args:
            items: List of ``ImportItem`` instances to convert.

        Returns:
            A list of dicts suitable for passing to the graph store's
            ``create_node`` method.
        """


__all__ = [
    "ImportConnector",
]
