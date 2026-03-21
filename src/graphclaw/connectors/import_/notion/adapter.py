"""graphclaw.connectors.import_.notion.adapter — Notion import connector.

Description
-----------
Implements ``ImportConnector`` for Notion using either the ``notion-client``
Python library (preferred) or raw ``httpx.AsyncClient`` calls to the Notion
REST API v1.

Supports importing from a single Notion database (configured via ``database_id``)
and discovering databases via the search endpoint.

Design Patterns
---------------
- Adapter: Translates between Notion page properties and the GraphClaw ImportItem.
- Graceful Degradation: Falls back to httpx if the ``notion-client`` lib is absent.

Public API
----------
- NotionImportConnector: ImportConnector for Notion.

Dependencies
------------
- notion-client: Optional Notion SDK (``pip install notion-client``).
- httpx: Async HTTP client fallback (required).

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.import_.base import ImportConnector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem

logger = logging.getLogger(__name__)

_NOTION_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _extract_rich_text(prop: dict | None) -> str:
    """Extract plain text from a Notion rich_text or title property."""
    if prop is None:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(t.get("plain_text", "") for t in items)


def _extract_select(prop: dict | None) -> str:
    """Extract the name from a Notion select property."""
    if prop is None:
        return ""
    return (prop.get("select") or {}).get("name", "")


def _extract_date(prop: dict | None) -> datetime | None:
    """Extract a datetime from a Notion date property."""
    if prop is None:
        return None
    date_obj = prop.get("date") or {}
    start_str = date_obj.get("start")
    if not start_str:
        return None
    try:
        if "T" in start_str:
            return datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        from datetime import date  # noqa: PLC0415

        d = date.fromisoformat(start_str)
        return datetime(d.year, d.month, d.day, tzinfo=UTC)
    except ValueError:
        return None


def _extract_people(prop: dict | None) -> str | None:
    """Extract the first person's name from a Notion people property."""
    if prop is None:
        return None
    people = prop.get("people", [])
    if not people:
        return None
    person = people[0]
    return person.get("name") or person.get("id")


def _extract_multi_select(prop: dict | None) -> list[str]:
    """Extract names from a Notion multi_select property."""
    if prop is None:
        return []
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def _notion_page_to_import_item(page: dict, database_id: str) -> ImportItem:
    """Map a Notion page dict to an ``ImportItem``."""
    props = page.get("properties", {})
    page_id = page.get("id", "")

    # Try common property names for title
    title = ""
    for key in ("Name", "Title", "Task", "Summary"):
        if key in props:
            title = _extract_rich_text(props[key])
            if title:
                break

    # Description from a Notes, Description, or Body property
    description = ""
    for key in ("Notes", "Description", "Body", "Content"):
        if key in props:
            description = _extract_rich_text(props.get(key, {}))
            if description:
                break

    # Status
    status_raw = _extract_select(props.get("Status") or props.get("state"))
    status_map = {
        "not started": "open",
        "in progress": "in_progress",
        "done": "done",
        "complete": "done",
        "completed": "done",
        "cancelled": "cancelled",
        "blocked": "blocked",
    }
    status = status_map.get(status_raw.lower(), "open") if status_raw else "open"

    # Priority
    priority_raw = _extract_select(props.get("Priority"))
    priority_map = {"high": "high", "medium": "medium", "low": "low", "urgent": "critical"}
    priority = priority_map.get(priority_raw.lower(), "medium") if priority_raw else "medium"

    # Due date
    due_date = _extract_date(props.get("Due") or props.get("Due Date"))

    # Assignee
    assignee = _extract_people(props.get("Assignee") or props.get("Assigned to"))

    # Labels (multi-select tags)
    labels = _extract_multi_select(props.get("Tags") or props.get("Labels"))

    url = page.get("url")

    return ImportItem(
        external_id=page_id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        assignee=assignee,
        labels=labels,
        url=url,
        source_system="notion",
        raw=page,
    )


class NotionImportConnector(ImportConnector):
    """Notion import connector using the Notion REST API.

    Args:
        config: ``ConnectorConfig`` with ``connector_type="notion"`` and the
            following ``credentials`` keys:

            - ``api_key``: Notion integration token (``secret_...``).
            - ``database_id``: Default Notion database ID to query.
    """

    connector_type = "notion"

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        creds = config.credentials
        self._api_key: str = creds["api_key"]
        self._database_id: str = creds.get("database_id", "")
        self._client: Any = None  # httpx.AsyncClient

    async def connect(self) -> None:
        """Initialise the httpx client with Notion auth headers."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "httpx is required for NotionImportConnector. Install with: pip install httpx"
            ) from exc

        self._client = httpx.AsyncClient(
            base_url=_NOTION_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        logger.info("NotionImportConnector connected")

    async def disconnect(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("NotionImportConnector disconnected")

    async def health_check(self) -> bool:
        """Return True if the Notion /users/me endpoint responds successfully."""
        try:
            resp = await self._client.get("/users/me")
            return resp.is_success
        except Exception as exc:  # noqa: BLE001
            logger.warning("NotionImportConnector health_check failed: %s", exc)
            return False

    async def list_projects(self) -> list[dict]:
        """Discover databases accessible to this integration via /search."""
        payload = {
            "filter": {"value": "database", "property": "object"},
        }
        resp = await self._client.post("/search", json=payload)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "id": db.get("id", ""),
                "name": _extract_rich_text({"title": db.get("title", [])}),
                "description": "",
            }
            for db in results
        ]

    async def fetch_items(
        self,
        project_id: str,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ImportBatch:
        """Query pages from a Notion database.

        Args:
            project_id: Notion database ID to query.  When empty, falls back
                to the ``database_id`` from the constructor credentials.
            since: Optional lower-bound on the page ``last_edited_time``.
            cursor: Notion ``start_cursor`` for pagination.
            limit: Maximum pages per page (Notion: ``page_size``).

        Returns:
            ``ImportBatch`` with items and pagination state.
        """
        db_id = project_id or self._database_id
        payload: dict[str, Any] = {"page_size": limit}
        if cursor:
            payload["start_cursor"] = cursor
        if since:
            payload["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": since.isoformat(),
                },
            }

        resp = await self._client.post(f"/databases/{db_id}/query", json=payload)
        resp.raise_for_status()
        data = resp.json()

        pages = data.get("results", [])
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

        items = [_notion_page_to_import_item(p, db_id) for p in pages]

        return ImportBatch(
            items=items,
            source_system="notion",
            project_id=db_id,
            fetched_at=datetime.now(tz=UTC),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def to_task_nodes(self, items: list[ImportItem]) -> list[dict]:
        """Convert ``ImportItem`` instances to TaskNode creation dicts."""
        return [
            {
                "title": item.title,
                "description": item.description,
                "task_type": "action",
                "status": item.status,
                "due_date": item.due_date,
                "priority": item.priority,
                "assignee": item.assignee,
                "labels": item.labels,
                "source_system": item.source_system,
                "external_id": item.external_id,
                "url": item.url,
            }
            for item in items
        ]


__all__ = [
    "NotionImportConnector",
]
