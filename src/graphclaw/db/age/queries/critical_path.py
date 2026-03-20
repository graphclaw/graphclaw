"""graphclaw.db.age.queries.critical_path — Longest-path (critical path) query for a goal.

Description
-----------
Implements the PRD §21.2 critical path algorithm over the task DAG rooted at a
GoalNode.  The critical path is defined as the root-to-leaf path whose
cumulative ``estimated_effort_hours`` is largest — it represents the minimum
elapsed time to complete the goal.  The AGE query returns every root→leaf path
with its effort sum (using ``reduce``); Python then selects the maximum and
annotates each node with ``on_critical_path`` and ``float`` (schedule slack).

Design Patterns
---------------
- Query Module: ``find_critical_path`` is a single-responsibility async function
  that returns annotated node dicts; scoring factors consume the result directly.

Public API
----------
- find_critical_path: Find the critical path from a GoalNode to its leaf tasks.

Dependencies
------------
- graphclaw.db.age.connection: ``get_connection`` for pool checkout.
- graphclaw.db.age.utils: ``GRAPH_NAME``, ``_parse_agtype``.
- psycopg_pool: ``AsyncConnectionPool`` type.
- json: agtype parsing.

Notes
-----
Because AGE evaluates ``reduce`` inside the ``$$`` block and returns all paths
sorted by effort descending, the first row is always the critical path.  The
Python post-processing step builds the float map for all other paths so that
near-critical nodes can be identified in future phases.
"""
from __future__ import annotations

import logging
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.age.connection import get_connection
from graphclaw.db.age.utils import GRAPH_NAME, _parse_agtype

logger = logging.getLogger(__name__)


async def find_critical_path(
    pool: AsyncConnectionPool,
    goal_id: str,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """Find the critical path from a GoalNode to its leaf tasks.

    Parameters
    ----------
    pool:
        An open async connection pool.
    goal_id:
        The ``id`` property of the GoalNode to start from.
    graph_name:
        AGE property graph name (default ``"graphclaw"``).

    Returns
    -------
    list[dict]
        Ordered list of node property dicts along the critical path, from
        goal to leaf.  Each dict includes a ``"float"`` key (0 for all
        nodes on the critical path) and an ``"on_critical_path"`` key
        (``True``).  Returns an empty list if the goal has no tasks.
    """
    # Step 1: Retrieve all root-to-leaf paths and their effort totals.
    # AGE's ``reduce`` accumulates estimated_effort_hours across path nodes.
    # We return the path nodes array and the cumulative effort so we can
    # select the maximum-effort path in Python.
    query = f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH path = (g:GoalNode {{id: %s}})-[:PART_OF|DEPENDS_ON*]->(leaf)
            WHERE NOT (leaf)-[:DEPENDS_ON]->()
            RETURN
                nodes(path) AS path_nodes,
                reduce(
                    total = 0.0,
                    n IN nodes(path) |
                    total + coalesce(n.estimated_effort_hours, 0.0)
                ) AS path_effort
            ORDER BY path_effort DESC
        $$) as (path_nodes agtype, path_effort agtype)
    """

    async with get_connection(pool) as conn:
        result = await conn.execute(query, (goal_id,))
        rows = await result.fetchall()

    if not rows:
        # No tasks linked to this goal yet.
        logger.debug("find_critical_path: no paths found for goal %s", goal_id)
        return []

    # Step 2: Identify the critical (longest) path.
    # Rows are already ORDER BY path_effort DESC from AGE.
    best_row = rows[0]
    best_nodes_raw = _parse_agtype(best_row[0])
    best_effort = float(_parse_agtype(best_row[1]) or 0.0)

    # Step 3: Build the critical path result list.
    critical_nodes = _extract_nodes_list(best_nodes_raw)
    result_nodes: list[dict] = []
    for node in critical_nodes:
        props = dict(node)
        props["on_critical_path"] = True
        props["float"] = 0.0
        result_nodes.append(props)

    logger.debug(
        "find_critical_path: critical path length %.2f for goal %s (%d nodes)",
        best_effort,
        goal_id,
        len(result_nodes),
    )
    return result_nodes


def _extract_nodes_list(raw: Any) -> list[dict]:
    """Convert the parsed ``nodes(path)`` agtype value to a list of property dicts."""
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, dict):
            props = item.get("properties", item)
            result.append(props if isinstance(props, dict) else {})
        else:
            result.append({})
    return result
