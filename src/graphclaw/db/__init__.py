# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db — Database layer public API with pluggable backends.

Description
-----------
Top-level public API for the GraphClaw database layer.  Exposes the ABCs,
factory functions, and the AGE connection helpers so that application code
has a single import target.

Design Patterns
---------------
- Facade: Aggregates re-exports from base, factory, and age.connection
  so callers never need to know the internal package structure.

Public API
----------
- GraphStore: ABC for node/edge CRUD operations.
- GraphQueryEngine: ABC for specialised graph traversal queries.
- create_graph_store: Factory to instantiate a GraphStore backend.
- create_query_engine: Factory to instantiate a GraphQueryEngine backend.
- create_pool: Create and open an async AGE connection pool.
- get_connection: Async context manager yielding a pool connection.

Dependencies
------------
- graphclaw.db.base: GraphStore, GraphQueryEngine ABCs.
- graphclaw.db.factory: create_graph_store, create_query_engine.
- graphclaw.db.age.connection: create_pool, get_connection.

Notes
-----
None.
"""

from __future__ import annotations

from graphclaw.db.age.connection import create_pool, get_connection
from graphclaw.db.base import GraphQueryEngine, GraphStore
from graphclaw.db.factory import create_graph_store, create_query_engine

__all__ = [
    "GraphStore",
    "GraphQueryEngine",
    "create_graph_store",
    "create_query_engine",
    "create_pool",
    "get_connection",
]
