"""Critical path query for GraphClaw.

Implements a modified Dijkstra / longest-path algorithm on the task DAG
rooted at a GoalNode.  The critical path is the sequence of nodes whose
cumulative ``estimated_effort_hours`` sum is the largest, representing the
minimum time to completion for the goal.

Algorithm (PRD §21.2):
1. From the GoalNode, traverse all PART_OF and DEPENDS_ON edges downstream
   to leaf nodes (nodes with no outgoing DEPENDS_ON edges).
2. For each path from goal to leaf, sum ``estimated_effort_hours``.
3. The path with the highest total is the critical path.
4. Nodes on the critical path get ``on_critical_path=True``; all others get
   ``float = critical_path_length - their_path_length``.

The AGE query returns every root→leaf path with its cumulative effort so we
can pick the maximum in Python (AGE's ``reduce`` handles per-path sums).
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

    # Collect path-effort pairs for all paths so we can compute float values
    # for nodes that appear on shorter paths.
    all_paths: list[tuple[list[dict], float]] = []
    for row in rows:
        nodes_raw = _parse_agtype(row[0])
        effort = float(_parse_agtype(row[1]) or 0.0)
        nodes_list = _extract_nodes_list(nodes_raw)
        all_paths.append((nodes_list, effort))

    # Build a map from node_id -> float value.
    # A node's float = critical_path_length - max_path_length_through_node.
    node_max_effort: dict[str, float] = {}
    for nodes_list, effort in all_paths:
        for node in nodes_list:
            nid = node.get("id", "")
            if nid and effort > node_max_effort.get(nid, -1.0):
                node_max_effort[nid] = effort

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
