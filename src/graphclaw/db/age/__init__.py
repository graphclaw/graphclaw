"""graphclaw.db.age — Apache AGE (Postgres) graph store backend.

Description
-----------
Package init for the AGE backend.  Re-exports the two public classes so
callers can do ``from graphclaw.db.age import AgeGraphStore``.

Design Patterns
---------------
- Facade: Re-exports hide the internal module structure from consumers.

Public API
----------
- AgeGraphStore: CRUD backend for Apache AGE / Postgres.
- AgeGraphQueryEngine: Traversal query engine for Apache AGE / Postgres.

Dependencies
------------
- graphclaw.db.age.repository: AgeGraphStore implementation.
- graphclaw.db.age.queries.engine: AgeGraphQueryEngine implementation.

Notes
-----
Import only what is needed to avoid loading psycopg / psycopg_pool at
module-discovery time.
"""

from __future__ import annotations

from graphclaw.db.age.queries.engine import AgeGraphQueryEngine
from graphclaw.db.age.repository import AgeGraphStore

__all__ = ["AgeGraphStore", "AgeGraphQueryEngine"]
