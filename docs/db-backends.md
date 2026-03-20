# Adding a Database Backend

GraphClaw's database layer is fully pluggable. Any graph database can be added by implementing the `GraphStore` and `GraphQueryEngine` ABCs.

## The ABCs

### GraphStore — `src/graphclaw/db/base.py`

Handles node and edge CRUD operations:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `upsert_node` | `(node: BaseNode) -> BaseNode` | Insert or update a node |
| `get_node` | `(node_id: str, node_type: str) -> BaseNode \| None` | Fetch a node by ID |
| `delete_node` | `(node_id: str, node_type: str) -> bool` | Delete a node |
| `upsert_edge` | `(edge: GraphEdge) -> GraphEdge` | Insert or update an edge |
| `get_edges` | `(from_id: str, rel_type: str \| None) -> list[GraphEdge]` | Get outgoing edges |
| `list_nodes` | `(node_type: str, filters: dict) -> list[BaseNode]` | List nodes by type |
| `traverse` | `(start_id: str, rel_type: str, depth: int) -> list[BaseNode]` | Graph traversal |
| `close` | `() -> None` | Release connections |

### GraphQueryEngine — `src/graphclaw/db/base.py`

Handles domain-specific graph queries (used by the scoring engine):

| Method | Purpose |
|--------|---------|
| `critical_path_nodes(goal_id)` | Find nodes on the critical path to a goal |
| `dependency_chain(task_id, depth)` | Get all transitive dependencies |
| `blocked_nodes(task_id)` | Get nodes currently blocking this task |
| `recently_completed(since, limit)` | Recently completed tasks for context |
| `goal_tasks(goal_id)` | All tasks associated with a goal |
| `scoring_context(task_ids)` | Batch-load scoring context for multiple tasks |
| `find_similar_tasks(embedding, limit)` | pgvector similarity search |

## Step-by-Step: Add a New Backend

### 1. Create the directory

```
src/graphclaw/db/
└── mydb/
    ├── __init__.py
    ├── store.py          # GraphStore implementation
    └── query_engine.py   # GraphQueryEngine implementation
```

### 2. Implement GraphStore

```python
# src/graphclaw/db/mydb/store.py
"""GraphStore implementation for MyDB."""
# graphclaw - Apache 2.0 license

from __future__ import annotations

from graphclaw.db.base import GraphStore, GraphQueryEngine
from graphclaw.models.base import BaseNode
from graphclaw.models.edges import GraphEdge


class MyDBGraphStore(GraphStore):
    """GraphStore backed by MyDB."""

    def __init__(self, connection_string: str) -> None:
        import mydb  # lazy import
        self._conn = mydb.connect(connection_string)
        self._query_engine = MyDBQueryEngine(self._conn)

    @property
    def query_engine(self) -> GraphQueryEngine:
        return self._query_engine

    async def upsert_node(self, node: BaseNode) -> BaseNode:
        # Translate node to MyDB format and upsert
        ...

    async def get_node(self, node_id: str, node_type: str) -> BaseNode | None:
        ...

    async def delete_node(self, node_id: str, node_type: str) -> bool:
        ...

    async def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        ...

    async def get_edges(self, from_id: str, rel_type: str | None = None) -> list[GraphEdge]:
        ...

    async def list_nodes(self, node_type: str, filters: dict | None = None) -> list[BaseNode]:
        ...

    async def traverse(self, start_id: str, rel_type: str, depth: int = 1) -> list[BaseNode]:
        ...

    async def close(self) -> None:
        await self._conn.close()
```

### 3. Implement GraphQueryEngine

```python
# src/graphclaw/db/mydb/query_engine.py
from graphclaw.db.base import GraphQueryEngine


class MyDBQueryEngine(GraphQueryEngine):
    def __init__(self, conn) -> None:
        self._conn = conn

    async def critical_path_nodes(self, goal_id: str) -> list[str]:
        # Run your graph traversal query
        ...

    async def dependency_chain(self, task_id: str, depth: int = 3) -> list[str]:
        ...

    async def blocked_nodes(self, task_id: str) -> list[str]:
        ...

    async def recently_completed(self, since, limit: int = 20):
        ...

    async def goal_tasks(self, goal_id: str):
        ...

    async def scoring_context(self, task_ids: list[str]):
        ...

    async def find_similar_tasks(self, embedding: list[float], limit: int = 5):
        ...
```

### 4. Export from `__init__.py`

```python
# src/graphclaw/db/mydb/__init__.py
from graphclaw.db.mydb.store import MyDBGraphStore

__all__ = ["MyDBGraphStore"]
```

### 5. Register in the factory

```python
# src/graphclaw/db/factory.py — add to the match block:

case "mydb":
    from graphclaw.db.mydb import MyDBGraphStore
    return MyDBGraphStore(**kwargs)
```

### 6. Add tests

```python
# tests/test_db/test_mydb.py
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from graphclaw.models.nodes import TaskNode


@pytest.fixture
def mock_mydb(monkeypatch):
    mock_sdk = MagicMock()
    mock_conn = AsyncMock()
    mock_sdk.connect.return_value = mock_conn
    monkeypatch.setitem(sys.modules, "mydb", mock_sdk)
    return mock_conn


@pytest.mark.asyncio
async def test_upsert_and_get_node(mock_mydb):
    from graphclaw.db.mydb import MyDBGraphStore
    store = MyDBGraphStore(connection_string="mydb://localhost/test")
    # test upsert and retrieval
    ...
```

## Using the Factory

```python
from graphclaw.db import create_graph_store

# Apache AGE (default)
store = create_graph_store(backend="age", pool=pool)

# Future backends
store = create_graph_store(backend="neo4j", uri="bolt://localhost:7687", auth=("neo4j", "password"))
store = create_graph_store(backend="mydb", connection_string="mydb://localhost/graphclaw")
```

## Current Backend: Apache AGE

The only production-ready backend is Apache AGE (`src/graphclaw/db/age/`):

- Uses `psycopg` async pool for PostgreSQL connections
- Cypher queries executed via `ag_catalog` extension
- pgvector for `find_similar_tasks` (embedding similarity)
- Connection setup in `src/graphclaw/db/connection.py`

See `src/graphclaw/db/age/` for the reference implementation to follow when building new backends.

## Node Type Mapping

GraphClaw has 17 node types defined in `src/graphclaw/models/nodes.py`. When adding a backend, you need to handle serialization for all types, or use the generic `BaseNode` representation and rely on Pydantic's discriminated union for deserialization:

```python
from graphclaw.models.nodes import NODE_TYPE_MAP  # maps node_type str -> Pydantic model
```
