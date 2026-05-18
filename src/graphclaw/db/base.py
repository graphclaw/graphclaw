# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class InsufficientPrivilegeError(Exception):
    """Raised when a principal attempts an operation it lacks privilege for.

    Wave 0 (FR-DEL-002): Raised by update_node() when agent_principal tries
    to update lifecycle fields (archived_at, purge_after, legal_hold, etc.).
    """


class ACLContextMissingError(Exception):
    """Raised when a repo call is made without a required caller_context.

    Wave 0 (FR-AL-001): All public repo methods require caller_context once
    mandatory ACL enforcement is rolled out.
    """


class GraphStore(ABC):
    """Abstract interface for graph node and edge CRUD operations.

    All backends (AGE, Neo4j, Neptune) implement this interface.

    Every concrete implementation MUST expose a ``principal_name`` property
    returning the string name of the service principal bound to its connection
    pool.  This value is logged with every DB operation for audit purposes
    (Wave 0 NFR-008).
    """

    @property
    def principal_name(self) -> str:
        """Return the principal name bound to this store instance."""
        return "unknown"

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

    async def list_nodes_by_user(self, label: str, user_id: str) -> list[dict]:
        """Return all vertices with *label* owned by *user_id* (filters on ``owned_by`` property).

        Default implementation delegates to ``list_nodes`` with an ``owned_by`` filter.
        Concrete backends may override for a more efficient query.
        """
        return await self.list_nodes(label, filters={"owned_by": user_id})

    async def list_nodes_for_goal(self, goal_id: str) -> list[dict]:
        """Return TaskNode vertices linked to *goal_id* via PART_OF edge.

        Default implementation returns an empty list.
        Concrete backends (AgeGraphStore) override with a graph traversal.
        """
        return []

    async def redirect_edges(self, from_id: str, to_id: str) -> int:
        """Re-point all edges from *from_id* to *to_id* (FR-ID-004 merge support).

        Default implementation is a no-op returning 0.
        Concrete backends (AgeGraphStore) override with a graph traversal.
        """
        return 0

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

    @abstractmethod
    async def get_nodes_bulk(self, node_ids: list[str]) -> dict[str, dict]: ...
