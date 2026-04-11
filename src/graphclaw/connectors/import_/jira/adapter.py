"""graphclaw.connectors.import_.jira.adapter — Jira import connector.

Description
-----------
Implements ``ImportConnector`` for Jira Cloud and Server using either the
``jira`` Python library (preferred) or a raw ``httpx.AsyncClient`` fallback.
Supports JQL-based item fetching with cursor-style pagination.

Design Patterns
---------------
- Adapter: Translates between Jira issue fields and the GraphClaw ImportItem model.
- Graceful Degradation: Falls back to httpx if the ``jira`` library is unavailable.

Public API
----------
- JiraImportConnector: ImportConnector for Jira.

Dependencies
------------
- jira: Optional Jira library (``pip install jira``).
- httpx: Async HTTP client fallback (required).

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.import_.base import ImportConnector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem

logger = logging.getLogger(__name__)

# Jira → GraphClaw priority normalisation map.
_PRIORITY_MAP: dict[str, str] = {
    "highest": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "lowest": "low",
    "blocker": "critical",
    "critical": "critical",
    "major": "high",
    "minor": "low",
    "trivial": "low",
}

# Jira status category → GraphClaw status normalisation map.
_STATUS_MAP: dict[str, str] = {
    "to do": "open",
    "open": "open",
    "backlog": "open",
    "in progress": "in_progress",
    "in review": "in_review",
    "done": "done",
    "closed": "done",
    "resolved": "done",
    "cancelled": "cancelled",
    "wont do": "cancelled",
}


def _normalise_priority(jira_priority: str | None) -> str:
    """Normalise a Jira priority name to a GraphClaw priority string."""
    if jira_priority is None:
        return "medium"
    return _PRIORITY_MAP.get(jira_priority.lower(), "medium")


def _normalise_status(jira_status: str | None) -> str:
    """Normalise a Jira status name to a GraphClaw status string."""
    if jira_status is None:
        return "open"
    return _STATUS_MAP.get(jira_status.lower(), "open")


def _parse_jira_date(date_str: str | None) -> datetime | None:
    """Parse a Jira date string (YYYY-MM-DD or ISO 8601) to a datetime."""
    if not date_str:
        return None
    try:
        if "T" in date_str:
            date_str = date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(date_str)
        # Date only
        from datetime import date  # noqa: PLC0415, timezone

        d = date.fromisoformat(date_str)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _raw_issue_to_import_item(issue: dict, server_url: str) -> ImportItem:
    """Map a Jira REST API issue dict to an ``ImportItem``."""
    fields = issue.get("fields", {})
    issue_key = issue.get("key", "")
    issue_id = issue.get("id", issue_key)

    assignee_data = fields.get("assignee") or {}
    assignee = assignee_data.get("displayName") or assignee_data.get("name")

    labels = fields.get("labels", [])
    components = [c.get("name", "") for c in fields.get("components", [])]
    all_labels = labels + components

    due_date = _parse_jira_date(fields.get("duedate"))
    priority_name = (fields.get("priority") or {}).get("name")
    status_name = (fields.get("status") or {}).get("name")

    description = fields.get("description") or ""
    if isinstance(description, dict):
        # Jira Cloud returns Atlassian Document Format (ADF) object
        description = description.get("text") or str(description)

    return ImportItem(
        external_id=issue_id,
        title=fields.get("summary", ""),
        description=description,
        status=_normalise_status(status_name),
        priority=_normalise_priority(priority_name),
        due_date=due_date,
        assignee=assignee,
        labels=all_labels,
        url=f"{server_url.rstrip('/')}/browse/{issue_key}",
        source_system="jira",
        raw=issue,
    )


class JiraImportConnector(ImportConnector):
    """Jira import connector.

    Tries to use the ``jira`` library; if not installed, falls back to raw
    ``httpx`` calls to the Jira REST API v3.

    Args:
        config: ``ConnectorConfig`` with ``connector_type="jira"`` and the
            following ``credentials`` keys:

            - ``server_url``: Base URL of the Jira instance
              (e.g. ``"https://myorg.atlassian.net"``).
            - ``username``: Jira username or email (for Basic auth).
            - ``api_token``: Jira API token (for Basic auth).
    """

    connector_type = "jira"

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        creds = config.credentials
        self._server_url: str = creds["server_url"].rstrip("/")
        self._username: str = creds["username"]
        self._api_token: str = creds["api_token"]
        self._client: Any = None  # httpx.AsyncClient

    async def connect(self) -> None:
        """Initialise the httpx client with Basic auth headers."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "httpx is required for JiraImportConnector. Install with: pip install httpx"
            ) from exc

        import base64  # noqa: PLC0415

        token = base64.b64encode(f"{self._username}:{self._api_token}".encode()).decode()

        self._client = httpx.AsyncClient(
            base_url=f"{self._server_url}/rest/api/3",
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        logger.info("JiraImportConnector connected to %s", self._server_url)

    async def disconnect(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("JiraImportConnector disconnected")

    async def health_check(self) -> bool:
        """Return True if the Jira /serverInfo endpoint responds successfully."""
        try:
            resp = await self._client.get("/serverInfo")
            return resp.is_success
        except Exception as exc:  # noqa: BLE001
            logger.warning("JiraImportConnector health_check failed: %s", exc)
            return False

    async def list_projects(self) -> list[dict]:
        """Return a list of all accessible Jira projects."""
        resp = await self._client.get("/project")
        resp.raise_for_status()
        projects = resp.json()
        return [
            {
                "id": p.get("key", p.get("id", "")),
                "name": p.get("name", ""),
                "description": p.get("description", ""),
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
        """Fetch issues from the given Jira project using JQL.

        Args:
            project_id: Jira project key (e.g. ``"MYPROJ"``).
            since: Optional lower-bound on ``updated`` field.
            cursor: Start-at index (as a string) for pagination.
            limit: Maximum results per page (Jira calls this ``maxResults``).

        Returns:
            ``ImportBatch`` with items, pagination cursor, and ``has_more`` flag.
        """
        start_at = int(cursor) if cursor else 0

        jql_parts = [f"project = {project_id}"]
        if since:
            jql_parts.append(f'updated >= "{since.strftime("%Y-%m-%d %H:%M")}"')
        jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"

        payload = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": limit,
            "fields": [
                "summary",
                "description",
                "status",
                "priority",
                "assignee",
                "duedate",
                "labels",
                "components",
                "issuetype",
            ],
        }

        resp = await self._client.post("/search", json=payload)
        resp.raise_for_status()
        data = resp.json()

        issues = data.get("issues", [])
        total = data.get("total", 0)
        returned = start_at + len(issues)
        has_more = returned < total
        next_cursor = str(returned) if has_more else None

        items = [_raw_issue_to_import_item(issue, self._server_url) for issue in issues]

        return ImportBatch(
            items=items,
            source_system="jira",
            project_id=project_id,
            fetched_at=datetime.now(tz=timezone.utc),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def to_task_nodes(self, items: list[ImportItem]) -> list[dict]:
        """Convert ``ImportItem`` instances to TaskNode creation dicts.

        Returns:
            A list of dicts with keys: ``title``, ``description``,
            ``task_type``, ``status``, ``due_date``.
        """
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
    "JiraImportConnector",
]
