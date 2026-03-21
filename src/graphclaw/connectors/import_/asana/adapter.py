"""graphclaw.connectors.import_.asana.adapter — Asana import connector.

Description
-----------
Implements ``ImportConnector`` for Asana using either the ``asana`` Python
library (preferred, if available) or raw ``httpx.AsyncClient`` calls to the
Asana REST API v1.0.

Design Patterns
---------------
- Adapter: Translates between Asana task fields and the GraphClaw ImportItem model.
- Graceful Degradation: Falls back to raw httpx if the ``asana`` library is absent.

Public API
----------
- AsanaImportConnector: ImportConnector for Asana.

Dependencies
------------
- asana: Optional Asana SDK (``pip install asana``).
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

_ASANA_BASE = "https://app.asana.com/api/1.0"

# Asana task fields to request in every API call.
_TASK_FIELDS = (
    "gid,name,notes,completed,due_on,assignee,tags,memberships,"
    "permalink_url,custom_fields,modified_at"
)


def _parse_asana_date(date_str: str | None) -> datetime | None:
    """Parse an Asana date string (YYYY-MM-DD) into a datetime."""
    if not date_str:
        return None
    try:
        from datetime import date  # noqa: PLC0415

        d = date.fromisoformat(date_str)
        return datetime(d.year, d.month, d.day, tzinfo=UTC)
    except ValueError:
        return None


def _asana_task_to_import_item(task: dict) -> ImportItem:
    """Map an Asana task dict to an ``ImportItem``."""
    status = "done" if task.get("completed") else "open"

    assignee_data = task.get("assignee") or {}
    assignee = assignee_data.get("name")

    tags = [t.get("name", "") for t in task.get("tags", []) if t.get("name")]

    return ImportItem(
        external_id=task.get("gid", ""),
        title=task.get("name", ""),
        description=task.get("notes", ""),
        status=status,
        priority="medium",
        due_date=_parse_asana_date(task.get("due_on")),
        assignee=assignee,
        labels=tags,
        url=task.get("permalink_url"),
        source_system="asana",
        raw=task,
    )


class AsanaImportConnector(ImportConnector):
    """Asana import connector using the Asana REST API.

    Args:
        config: ``ConnectorConfig`` with ``connector_type="asana"`` and the
            following ``credentials`` keys:

            - ``access_token``: Asana personal access token.
            - ``workspace_gid``: The GID of the Asana workspace to query.
    """

    connector_type = "asana"

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        creds = config.credentials
        self._access_token: str = creds["access_token"]
        self._workspace_gid: str = creds["workspace_gid"]
        self._client: Any = None  # httpx.AsyncClient

    async def connect(self) -> None:
        """Initialise the httpx client with Bearer auth."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "httpx is required for AsanaImportConnector. Install with: pip install httpx"
            ) from exc

        self._client = httpx.AsyncClient(
            base_url=_ASANA_BASE,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        )
        logger.info("AsanaImportConnector connected")

    async def disconnect(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("AsanaImportConnector disconnected")

    async def health_check(self) -> bool:
        """Return True if the Asana /users/me endpoint responds successfully."""
        try:
            resp = await self._client.get("/users/me")
            return resp.is_success
        except Exception as exc:  # noqa: BLE001
            logger.warning("AsanaImportConnector health_check failed: %s", exc)
            return False

    async def list_projects(self) -> list[dict]:
        """Return all projects in the configured workspace."""
        resp = await self._client.get(
            "/projects",
            params={"workspace": self._workspace_gid, "opt_fields": "gid,name,notes"},
        )
        resp.raise_for_status()
        projects = resp.json().get("data", [])
        return [
            {
                "id": p.get("gid", ""),
                "name": p.get("name", ""),
                "description": p.get("notes", ""),
            }
            for p in projects
        ]

    async def fetch_items(
        self,
        project_id: str,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ImportBatch:
        """Fetch tasks from the given Asana project.

        Args:
            project_id: Asana project GID.
            since: Optional lower-bound filter on ``modified_at``.
            cursor: Asana pagination offset token.
            limit: Maximum tasks per page.

        Returns:
            ``ImportBatch`` with items and pagination state.
        """
        params: dict[str, Any] = {
            "project": project_id,
            "opt_fields": _TASK_FIELDS,
            "limit": limit,
        }
        if cursor:
            params["offset"] = cursor
        if since:
            params["modified_since"] = since.isoformat()

        resp = await self._client.get("/tasks", params=params)
        resp.raise_for_status()
        data = resp.json()

        tasks = data.get("data", [])
        next_page = data.get("next_page") or {}
        next_cursor = next_page.get("offset")
        has_more = next_cursor is not None

        items = [_asana_task_to_import_item(t) for t in tasks]

        return ImportBatch(
            items=items,
            source_system="asana",
            project_id=project_id,
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
    "AsanaImportConnector",
]
