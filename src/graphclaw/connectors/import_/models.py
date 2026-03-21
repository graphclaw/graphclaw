"""graphclaw.connectors.import_.models — Data models for import connectors.

Description
-----------
Defines the shared data transfer objects used by all import connector
implementations: ``ImportItem`` (a single imported task/issue) and
``ImportBatch`` (a paginated batch of items from a source system).

``ImportItem`` is a frozen dataclass for value-based equality and safe caching.
``ImportBatch`` is a regular dataclass since its ``has_more`` and ``next_cursor``
fields describe pagination state that callers may want to mutate.

Public API
----------
- ImportItem: Immutable dataclass representing a single imported task or issue.
- ImportBatch: Dataclass representing a paginated batch of imported items.

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
class ImportItem:
    """A single item (task, issue, page) imported from an external system.

    Attributes
    ----------
    external_id:
        The unique identifier in the source system.
    title:
        Short human-readable title or summary.
    description:
        Optional long-form description, body text, or markdown content.
    status:
        Normalised status string (e.g. ``"open"``, ``"in_progress"``, ``"done"``).
    priority:
        Normalised priority string (e.g. ``"low"``, ``"medium"``, ``"high"``).
    due_date:
        Optional deadline (timezone-aware recommended).
    assignee:
        Optional display name or email of the assigned user.
    labels:
        List of tag/label strings.
    url:
        Optional permalink to the item in the source system.
    source_system:
        Identifier of the source system (e.g. ``"jira"``, ``"asana"``).
    raw:
        The full original API response object for lossless storage.
    """

    external_id: str
    title: str
    description: str = ""
    status: str = "open"
    priority: str = "medium"
    due_date: datetime | None = None
    assignee: str | None = None
    labels: list[str] = field(default_factory=list)
    url: str | None = None
    source_system: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ImportBatch:
    """A paginated batch of items returned by ``ImportConnector.fetch_items``.

    Attributes
    ----------
    items:
        The list of ``ImportItem`` instances in this batch.
    source_system:
        Identifier of the source system that produced this batch.
    project_id:
        The project/board/database ID that was queried.
    fetched_at:
        UTC timestamp when this batch was fetched.
    next_cursor:
        Opaque pagination cursor to pass on the next ``fetch_items`` call.
        ``None`` when there are no more pages.
    has_more:
        ``True`` when additional pages are available beyond this batch.
    """

    items: list[ImportItem]
    source_system: str
    project_id: str
    fetched_at: datetime
    next_cursor: str | None = None
    has_more: bool = False


__all__ = [
    "ImportItem",
    "ImportBatch",
]
