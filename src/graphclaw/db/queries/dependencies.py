"""Dependency traversal queries for GraphClaw.

Three query functions covering the dependency graph:

- ``get_downstream_dependents`` — all tasks that (transitively) depend on a
  given node (i.e. would be blocked if this node were delayed).
- ``get_upstream_blockers`` — all tasks this node (transitively) depends on
  (i.e. must complete before this node can start).
- ``get_blocked_root_causes`` — for every currently BLOCKED task in the
  graph, find the deepest upstream task that has no further dependencies
  (the root cause).

All queries use variable-length Cypher path patterns (``*``) which AGE
resolves recursively up to an internal depth limit.  For very deep graphs
consider adding a ``*..N`` upper bound.

NOTE: AGE does not support parameterized queries ($1) inside $$ blocks.
All values are embedded directly into the Cypher string with escaping.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.connection import get_connection

logger = logging.getLogger(__name__)

GRAPH_NAME = "graphclaw"


def _parse_agtype(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)


def _row_to_dict(row: tuple, keys: list[str]) -> dict:
    """Zip column names with parsed agtype values from a result row."""
    return {k: _parse_agtype(v) for k, v in zip(keys, row)}


def _escape(value: str) -> str:
    """Escape a string for safe embedding inside Cypher string literals."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


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
                MATCH (anchor {{id: '{eid}'}})<-[:DEPENDS_ON*]-(downstream)
                WHERE downstream.id <> '{eid}'
                RETURN DISTINCT downstream.id AS id,
                               downstream.state AS state,
                               downstream.title AS title
            $$) as (id agtype, state agtype, title agtype)
            """
        )
        rows = await result.fetchall()

    dependents = [_row_to_dict(row, ["id", "state", "title"]) for row in rows]
    logger.debug(
        "get_downstream_dependents: %d results for %s", len(dependents), node_id
    )
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
                MATCH (anchor {{id: '{eid}'}})-[:DEPENDS_ON*]->(upstream)
                WHERE upstream.id <> '{eid}'
                RETURN DISTINCT upstream.id AS id,
                               upstream.state AS state,
                               upstream.title AS title
            $$) as (id agtype, state agtype, title agtype)
            """
        )
        rows = await result.fetchall()

    blockers = [_row_to_dict(row, ["id", "state", "title"]) for row in rows]
    logger.debug(
        "get_upstream_blockers: %d results for %s", len(blockers), node_id
    )
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
                MATCH (blocked {{state: 'BLOCKED'}})-[:DEPENDS_ON|BLOCKS*]->(root)
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
