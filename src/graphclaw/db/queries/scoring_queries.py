"""Scoring-related graph queries for GraphClaw.

These queries feed the scoring engine with the data it needs to compute
priority scores for tasks.  All three functions are read-only and return
plain Python dicts so the scoring layer has no direct database dependency.

Functions
---------
get_active_tasks_for_scoring
    All non-terminal, non-snoozed tasks for a specific user — the candidate
    set for a scoring cycle.
get_constraints_for_task
    ConstraintNodes linked to a task via APPLIES_TO edges.
get_assigned_resource
    The ResourceNode assigned to a task via an ASSIGNED_TO edge.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.connection import get_connection

logger = logging.getLogger(__name__)

GRAPH_NAME = "graphclaw"

# Task states that are considered terminal — scoring skips these.
_TERMINAL_STATES = {"COMPLETE", "CANCELLED", "ARCHIVED"}

# Task state indicating the task has been snoozed — also skipped by default.
_SNOOZED_STATE = "SNOOZED"


def _parse_agtype(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)


def _extract_properties(agtype_node: Any) -> dict:
    parsed = _parse_agtype(agtype_node)
    if isinstance(parsed, dict):
        return parsed.get("properties", parsed)
    return {}


async def get_active_tasks_for_scoring(
    pool: AsyncConnectionPool,
    user_id: str,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """Return all scoreable tasks for the given user.

    A task is scoreable when:
    - It is owned by ``user_id`` (via OWNED_BY edge to a UserNode)
    - Its state is not in the terminal set (COMPLETE, CANCELLED, ARCHIVED)
    - Its state is not SNOOZED

    Covers all task vertex labels by matching on the OWNED_BY relationship
    rather than restricting to a single label.

    Parameters
    ----------
    pool:
        An open async connection pool.
    user_id:
        ``id`` property of the UserNode whose tasks should be fetched.
    graph_name:
        AGE property graph name.

    Returns
    -------
    list[dict]
        Full property dicts for each active task.
    """
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (t)-[:OWNED_BY]->(u:UserNode {{id: %s}})
                WHERE t.state <> 'COMPLETE'
                  AND t.state <> 'CANCELLED'
                  AND t.state <> 'ARCHIVED'
                  AND t.state <> 'SNOOZED'
                RETURN t
            $$) as (t agtype)
            """,
            (user_id,),
        )
        rows = await result.fetchall()

    tasks = [_extract_properties(row[0]) for row in rows]
    logger.debug(
        "get_active_tasks_for_scoring: %d tasks for user %s", len(tasks), user_id
    )
    return tasks


async def get_constraints_for_task(
    pool: AsyncConnectionPool,
    task_id: str,
    graph_name: str = GRAPH_NAME,
) -> list[dict]:
    """Return all ConstraintNodes that apply to the given task.

    A ConstraintNode is linked to a task via an APPLIES_TO edge:
    ``(constraint:ConstraintNode)-[:APPLIES_TO]->(task)``.

    Parameters
    ----------
    pool:
        An open async connection pool.
    task_id:
        ``id`` property of the task node.
    graph_name:
        AGE property graph name.

    Returns
    -------
    list[dict]
        Full property dicts for each ConstraintNode that applies to this
        task.  Returns an empty list if no constraints are linked.
    """
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (c:ConstraintNode)-[:APPLIES_TO]->(t {{id: %s}})
                RETURN c
            $$) as (c agtype)
            """,
            (task_id,),
        )
        rows = await result.fetchall()

    constraints = [_extract_properties(row[0]) for row in rows]
    logger.debug(
        "get_constraints_for_task: %d constraints for task %s",
        len(constraints),
        task_id,
    )
    return constraints


async def get_assigned_resource(
    pool: AsyncConnectionPool,
    task_id: str,
    graph_name: str = GRAPH_NAME,
) -> dict | None:
    """Return the ResourceNode assigned to the given task.

    A task is linked to a resource via an ASSIGNED_TO edge:
    ``(task)-[:ASSIGNED_TO]->(resource:ResourceNode)``.

    Parameters
    ----------
    pool:
        An open async connection pool.
    task_id:
        ``id`` property of the task node.
    graph_name:
        AGE property graph name.

    Returns
    -------
    dict | None
        Property dict of the assigned ResourceNode, or ``None`` if no
        resource is assigned.
    """
    async with get_connection(pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (t {{id: %s}})-[:ASSIGNED_TO]->(r:ResourceNode)
                RETURN r
            $$) as (r agtype)
            """,
            (task_id,),
        )
        row = await result.fetchone()

    if row is None:
        return None

    resource = _extract_properties(row[0])
    logger.debug(
        "get_assigned_resource: found resource %s for task %s",
        resource.get("id"),
        task_id,
    )
    return resource
