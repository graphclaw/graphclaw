# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.conftest — Shared fakes and fixtures for API route tests.

Provides in-memory ``FakeGraphStore`` and ``FakeStorageClient`` implementations
so API tests can override FastAPI dependencies without hitting real backends.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from graphclaw.db.base import GraphStore
from graphclaw.infra.storage import StorageClient


class FakeGraphStore(GraphStore):
    """In-memory GraphStore for use in FastAPI dependency overrides."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    def clear(self) -> None:
        self._nodes.clear()
        self._edges.clear()

    async def create_node(self, node) -> dict:
        d = node.model_dump(mode="json") if hasattr(node, "model_dump") else dict(node)
        self._nodes[d["id"]] = d
        return d

    async def get_node(self, node_id: str) -> dict | None:
        return self._nodes.get(node_id)

    async def update_node(self, node_id: str, updates: dict) -> dict | None:
        if node_id not in self._nodes:
            return None
        self._nodes[node_id].update(updates)
        return self._nodes[node_id]

    async def delete_node(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)

    async def list_nodes(self, label: str, filters: dict | None = None) -> list[dict]:
        results = list(self._nodes.values())
        if filters:
            for k, v in filters.items():
                results = [n for n in results if n.get(k) == v]
        return results

    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict | None = None,
    ) -> dict:
        edge: dict = {
            "id": f"edge-{len(self._edges)}",
            "source_id": source_id,
            "target_id": target_id,
            "edge_type": edge_type,
            **(properties or {}),
        }
        self._edges.append(edge)
        return edge

    async def get_edges(
        self,
        node_id: str,
        direction: str = "out",
        edge_type: str | None = None,
    ) -> list[dict]:
        if direction == "out":
            edges = [e for e in self._edges if e.get("source_id") == node_id]
        elif direction == "in":
            edges = [e for e in self._edges if e.get("target_id") == node_id]
        else:
            edges = [
                e
                for e in self._edges
                if e.get("source_id") == node_id or e.get("target_id") == node_id
            ]
        if edge_type:
            edges = [e for e in edges if e.get("edge_type") == edge_type]
        return edges

    async def delete_edge(self, edge_id: str) -> None:
        self._edges = [e for e in self._edges if e.get("id") != edge_id]


class FakeStorageClient(StorageClient):
    """In-memory StorageClient for use in FastAPI dependency overrides."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def clear(self) -> None:
        self._data.clear()

    async def read(self, path: str) -> bytes:
        if path not in self._data:
            raise FileNotFoundError(f"Object not found: {path}")
        return self._data[path]

    async def write(self, path: str, data: bytes, content_type: str = "text/plain") -> None:
        self._data[path] = data

    async def delete(self, path: str) -> None:
        self._data.pop(path, None)

    async def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self._data if k.startswith(prefix))

    async def exists(self, path: str) -> bool:
        return path in self._data


class FakeNotificationService:
    """In-memory NotificationService for use in FastAPI dependency overrides."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def clear(self) -> None:
        self._rows.clear()

    async def create(
        self,
        user_id: str,
        event_type: str,
        title: str,
        body: str = "",
        metadata: dict | None = None,
    ) -> str:
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "event_type": event_type,
            "title": title,
            "body": body,
            "metadata": metadata or {},
            "is_read": False,
            "read_at": None,
            "created_at": datetime.now(timezone.utc),
            "dismissed_at": None,
        }
        self._rows.append(row)
        return row["id"]

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 30,
        cursor: str | None = None,
    ) -> tuple[list[dict], int, str | None]:
        visible = [r for r in self._rows if r["user_id"] == user_id and r["dismissed_at"] is None]
        if cursor:
            visible = [r for r in visible if r["created_at"].isoformat() < cursor]
        unread = sum(1 for r in visible if not r["is_read"])
        page = visible[:limit]
        next_cursor = page[-1]["created_at"].isoformat() if len(visible) > limit else None
        return page, unread, next_cursor

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        for r in self._rows:
            if r["id"] == notification_id and r["user_id"] == user_id and r["dismissed_at"] is None:
                r["is_read"] = True
                return True
        return False

    async def mark_all_read(self, user_id: str) -> int:
        count = 0
        for r in self._rows:
            if r["user_id"] == user_id and not r["is_read"] and r["dismissed_at"] is None:
                r["is_read"] = True
                count += 1
        return count

    async def dismiss(self, notification_id: str, user_id: str) -> bool:
        for r in self._rows:
            if r["id"] == notification_id and r["user_id"] == user_id and r["dismissed_at"] is None:
                r["dismissed_at"] = datetime.now(timezone.utc)
                return True
        return False

    async def unread_count(self, user_id: str) -> int:
        return sum(
            1
            for r in self._rows
            if r["user_id"] == user_id and not r["is_read"] and r["dismissed_at"] is None
        )
