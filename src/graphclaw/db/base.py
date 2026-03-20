"""graphclaw.db.base — Abstract base classes for the graph database layer.

Description
-----------
Defines the two ABCs that all graph storage backends must implement:
``GraphStore`` for node and edge CRUD operations, and ``GraphQueryEngine``
for specialised graph traversal queries (dependency analysis, critical path,
scoring).  Backends (AGE, Neo4j, Neptune) each provide concrete subclasses;
the rest of the application depends only on these interfaces.

Design Patterns
---------------
- Abstract Base Class: Both ABCs define the minimal contract so production
  (AGE) and future backends are interchangeable without touching call sites.
- Interface Segregation: CRUD and query concerns are separated into two
  distinct ABCs so callers can depend on only what they need.

Public API
----------
- GraphStore: ABC for node/edge CRUD operations.
- GraphQueryEngine: ABC for specialised graph traversal queries.

Dependencies
------------
- abc: ABC, abstractmethod.
- typing: Any for untyped node model parameters.

Notes
-----
All methods are declared async to keep the interface consistent with I/O-bound
implementations.  Synchronous backends must wrap their operations in
``asyncio.to_thread`` or similar.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GraphStore(ABC):
    """Abstract interface for graph node and edge CRUD operations.

    All backends (AGE, Neo4j, Neptune) implement this interface.
    """

    @abstractmethod
    async def create_node(self, node: Any) -> dict: ...

    @abstractmethod
    async def get_node(self, node_id: str) -> dict | None: ...

    @abstractmethod
    async def update_node(self, node_id: str, updates: dict) -> dict | None: ...

    @abstractmethod
    async def delete_node(self, node_id: str) -> None: ...

    @abstractmethod
    async def list_nodes(self, label: str, filters: dict | None = None) -> list[dict]: ...

    @abstractmethod
    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict | None = None,
    ) -> dict: ...

    @abstractmethod
    async def get_edges(
        self,
        node_id: str,
        direction: str = "out",
        edge_type: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    async def delete_edge(self, edge_id: str) -> None: ...


class GraphQueryEngine(ABC):
    """Abstract interface for specialised graph traversal queries.

    Backends implement these for dependency analysis, critical path, etc.
    """

    @abstractmethod
    async def get_downstream_dependents(self, node_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_upstream_blockers(self, node_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_blocked_root_causes(self) -> list[dict]: ...

    @abstractmethod
    async def find_critical_path(self, goal_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_active_tasks_for_scoring(self, user_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_constraints_for_task(self, task_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_assigned_resource(self, task_id: str) -> dict | None: ...
