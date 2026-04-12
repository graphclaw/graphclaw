"""tests.test_api.conftest — Shared fakes and fixtures for API route tests.

Provides in-memory ``FakeGraphStore`` and ``FakeStorageClient`` implementations
so API tests can override FastAPI dependencies without hitting real backends.
"""

from __future__ import annotations

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
