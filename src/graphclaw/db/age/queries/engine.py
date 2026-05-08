# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.age.queries.engine — AGE implementation of GraphQueryEngine.

Description
-----------
Provides ``AgeGraphQueryEngine``, the AGE backend implementation of the
``GraphQueryEngine`` ABC.  It wraps the seven standalone async query functions
(from dependencies, critical_path, and scoring_queries modules) behind the
unified interface, passing the pool and graph name on each call.

Design Patterns
---------------
- Adapter: Wraps standalone functions behind the GraphQueryEngine ABC interface.
- Plugin / Strategy: Inherits from GraphQueryEngine so it can be swapped for
  a different backend without changing call sites.

Public API
----------
- AgeGraphQueryEngine: GraphQueryEngine implementation for Apache AGE / Postgres.

Dependencies
------------
- graphclaw.db.base: ``GraphQueryEngine`` ABC.
- graphclaw.db.age.utils: ``GRAPH_NAME`` default constant.
- graphclaw.db.age.queries.dependencies: dependency traversal functions.
- graphclaw.db.age.queries.critical_path: find_critical_path function.
- graphclaw.db.age.queries.scoring_queries: scoring support functions.
- psycopg_pool: ``AsyncConnectionPool`` type annotation.

Notes
-----
The pool and graph_name are stored on construction and forwarded to each
standalone function.  This avoids the need for callers to pass the pool on
every individual query call.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.age.connection import get_connection
from graphclaw.db.age.queries.critical_path import find_critical_path
from graphclaw.db.age.queries.dependencies import (
    get_blocked_root_causes,
    get_downstream_dependents,
    get_upstream_blockers,
)
from graphclaw.db.age.queries.scoring_queries import (
    get_active_tasks_for_scoring,
    get_assigned_resource,
    get_constraints_for_task,
)
from graphclaw.db.age.utils import GRAPH_NAME, _escape, _extract_properties
from graphclaw.db.base import GraphQueryEngine


class AgeGraphQueryEngine(GraphQueryEngine):
    """AGE backend implementation of the GraphQueryEngine ABC.

    Parameters
    ----------
    pool:
        An open ``AsyncConnectionPool`` (created via
        ``graphclaw.db.age.connection.create_pool``).
    graph_name:
        Name of the AGE property graph.  Defaults to ``"graphclaw"``.
    """

    def __init__(self, pool: AsyncConnectionPool, graph_name: str = GRAPH_NAME) -> None:
        self._pool = pool
        self._graph_name = graph_name

    async def get_downstream_dependents(self, node_id: str) -> list[dict]:
        return await get_downstream_dependents(self._pool, node_id, self._graph_name)

    async def get_upstream_blockers(self, node_id: str) -> list[dict]:
        return await get_upstream_blockers(self._pool, node_id, self._graph_name)

    async def get_blocked_root_causes(self) -> list[dict]:
        return await get_blocked_root_causes(self._pool, self._graph_name)

    async def find_critical_path(self, goal_id: str) -> list[dict]:
        return await find_critical_path(self._pool, goal_id, self._graph_name)

    async def get_active_tasks_for_scoring(self, user_id: str) -> list[dict]:
        return await get_active_tasks_for_scoring(self._pool, user_id, self._graph_name)

    async def get_constraints_for_task(self, task_id: str) -> list[dict]:
        return await get_constraints_for_task(self._pool, task_id, self._graph_name)

    async def get_assigned_resource(self, task_id: str) -> dict | None:
        return await get_assigned_resource(self._pool, task_id, self._graph_name)

    async def get_nodes_bulk(self, node_ids: list[str]) -> dict[str, dict]:
        if not node_ids:
            return {}
        escaped_ids = ", ".join(f"'{_escape(nid)}'" for nid in node_ids)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph_name}', $$
                    MATCH (n)
                    WHERE n.id IN [{escaped_ids}]
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows = await result.fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            props = _extract_properties(row[0])
            nid = props.get("id")
            if nid:
                out[str(nid)] = props
        return out
