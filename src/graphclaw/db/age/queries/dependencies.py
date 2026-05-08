# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.age.queries.dependencies — Transitive dependency traversal queries.

Description
-----------
Provides three read-only Cypher queries that traverse the DEPENDS_ON and BLOCKS
edge types in the task graph.  These results feed the scoring engine's dependency
weight factor and the agent's blocker-resolution reporting.  All queries use
variable-length path patterns (``*``) so AGE resolves the full transitive closure
automatically, up to its internal depth limit.

Design Patterns
---------------
- Query Module: All functions are pure async query functions with no side effects,
  returning plain Python dicts to keep the scoring layer DB-agnostic.

Public API
----------
- get_downstream_dependents: All tasks that (transitively) depend on a given node.
- get_upstream_blockers: All tasks a given node (transitively) depends on.
- get_blocked_root_causes: For every BLOCKED task, the deepest upstream root cause.

Dependencies
------------
- graphclaw.db.age.connection: ``get_connection`` for pool checkout.
- graphclaw.db.age.utils: ``GRAPH_NAME``, ``_escape``, ``_parse_agtype``.
- psycopg_pool: ``AsyncConnectionPool`` type.
- json: agtype parsing.

Notes
-----
AGE does not support ``$1`` bind parameters inside ``$$ ... $$`` blocks.  All
node ID values are escaped via ``_escape()`` before embedding.  For very deep
dependency graphs (depth > 100), consider adding an ``*..N`` upper bound to
the variable-length path patterns to avoid excessive traversal time.
"""

from __future__ import annotations

import logging

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.age.connection import get_connection
from graphclaw.db.age.utils import GRAPH_NAME, _escape, _parse_agtype

logger = logging.getLogger(__name__)


def _row_to_dict(row: tuple, keys: list[str]) -> dict:
    """Zip column names with parsed agtype values from a result row."""
    return {k: _parse_agtype(v) for k, v in zip(keys, row)}


async def get_downstream_dependents(
    pool: AsyncConnectionPool,
    node_id: str,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """Return all tasks that (transitively) depend on ``node_id``.

    A task T is a downstream dependent of N if there exists a path
    T-[:DEPENDS_ON*]->N (T cannot start until N is done).
    """
    eid = _escape(node_id)
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (anchor {{id: '{eid}'}})<-[:DEPENDS_ON*..20]-(downstream)
                WHERE downstream.id <> '{eid}'
                RETURN DISTINCT downstream.id AS id,
                               downstream.state AS state,
                               downstream.title AS title
            $$) as (id agtype, state agtype, title agtype)
            """
        )
        rows = await result.fetchall()

    dependents = [_row_to_dict(row, ["id", "state", "title"]) for row in rows]
    logger.debug("get_downstream_dependents: %d results for %s", len(dependents), node_id)
    return dependents


async def get_upstream_blockers(
    pool: AsyncConnectionPool,
    node_id: str,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """Return all tasks that this node (transitively) depends on.

    A task U is an upstream blocker of N if there exists a path
    N-[:DEPENDS_ON*]->U (N cannot start until U is done).
    """
    eid = _escape(node_id)
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (anchor {{id: '{eid}'}})-[:DEPENDS_ON*..20]->(upstream)
                WHERE upstream.id <> '{eid}'
                RETURN DISTINCT upstream.id AS id,
                               upstream.state AS state,
                               upstream.title AS title
            $$) as (id agtype, state agtype, title agtype)
            """
        )
        rows = await result.fetchall()

    blockers = [_row_to_dict(row, ["id", "state", "title"]) for row in rows]
    logger.debug("get_upstream_blockers: %d results for %s", len(blockers), node_id)
    return blockers


async def get_blocked_root_causes(
    pool: AsyncConnectionPool,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """For every BLOCKED task in the graph, find its root-cause task.

    A root cause is the deepest upstream dependency that has no further
    DEPENDS_ON outgoing edges (i.e. a leaf in the dependency DAG).
    """
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (blocked {{state: 'BLOCKED'}})-[:DEPENDS_ON|BLOCKS*..20]->(root)
                WHERE NOT (root)-[:DEPENDS_ON]->()
                RETURN DISTINCT
                    blocked.id       AS blocked_id,
                    root.id          AS root_id,
                    root.state       AS root_state,
                    root.assigned_to AS root_assignee
            $$) as (
                blocked_id  agtype,
                root_id     agtype,
                root_state  agtype,
                root_assignee agtype
            )
            """
        )
        rows = await result.fetchall()

    keys = ["blocked_id", "root_id", "root_state", "root_assignee"]
    causes = [_row_to_dict(row, keys) for row in rows]
    logger.debug("get_blocked_root_causes: %d results", len(causes))
    return causes
