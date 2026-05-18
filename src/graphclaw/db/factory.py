# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.factory — Factory functions for graph store backends.

Description
-----------
Provides ``create_graph_store`` and ``create_query_engine`` factory functions
that instantiate the correct backend implementation based on a ``backend``
string.  Currently supports ``"age"`` (Apache AGE / Postgres).  Future backends
(Neo4j, Neptune) will be added here without changing any call sites.

Design Patterns
---------------
- Factory Function: Centralises backend selection so callers never import
  concrete classes directly; they depend only on the ABCs.
- Plugin Registry: The ``backend`` string acts as a registry key, making it
  straightforward to add new backends.

Public API
----------
- create_graph_store: Instantiate a GraphStore for the requested backend.
- create_query_engine: Instantiate a GraphQueryEngine for the requested backend.

Dependencies
------------
- graphclaw.db.base: GraphStore, GraphQueryEngine ABCs (return type annotations).
- graphclaw.db.age: AgeGraphStore, AgeGraphQueryEngine (imported lazily).

Notes
-----
Backend implementations are imported lazily (inside the if-branch) so that
unused backends do not pull in their dependencies at module import time.
"""

from __future__ import annotations

from graphclaw.db.base import GraphQueryEngine, GraphStore


def create_graph_store(backend: str = "age", **kwargs) -> GraphStore:
    """Instantiate a GraphStore for the requested backend.

    Parameters
    ----------
    backend:
        Backend identifier string.  Currently only ``"age"`` is supported.
    **kwargs:
        Backend-specific keyword arguments.  For ``"age"``: ``pool``
        (required) and ``graph_name`` (optional, defaults to ``"graphclaw"``).

    Returns
    -------
    GraphStore
        A concrete GraphStore instance for the chosen backend.

    Raises
    ------
    ValueError
        If ``backend`` is not a recognised backend identifier.
    """
    if backend == "age":
        from graphclaw.db.age import AgeGraphStore

        return AgeGraphStore(pool=kwargs["pool"], graph_name=kwargs.get("graph_name", "graphclaw"))
    raise ValueError(f"Unknown database backend: {backend!r}. Available: 'age'")


def create_query_engine(backend: str = "age", **kwargs) -> GraphQueryEngine:
    """Instantiate a GraphQueryEngine for the requested backend.

    Parameters
    ----------
    backend:
        Backend identifier string.  Currently only ``"age"`` is supported.
    **kwargs:
        Backend-specific keyword arguments.  For ``"age"``: ``pool``
        (required) and ``graph_name`` (optional, defaults to ``"graphclaw"``).

    Returns
    -------
    GraphQueryEngine
        A concrete GraphQueryEngine instance for the chosen backend.

    Raises
    ------
    ValueError
        If ``backend`` is not a recognised backend identifier.
    """
    if backend == "age":
        from graphclaw.db.age import AgeGraphQueryEngine

        return AgeGraphQueryEngine(
            pool=kwargs["pool"], graph_name=kwargs.get("graph_name", "graphclaw")
        )
    raise ValueError(f"Unknown database backend: {backend!r}. Available: 'age'")
