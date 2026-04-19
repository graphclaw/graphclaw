"""graphclaw.api.graph — Graph node and edge CRUD endpoints for the cockpit.

Description
-----------
Provides the complete Graph API consumed by the cockpit canvas (PRD §02, §12).
All reads and writes go through the ``GraphStore`` / ``GraphQueryEngine`` ABCs
so the backend is database-agnostic.

Endpoints
---------
Goals
  GET  /app/v1/graph/goals                   — List GoalNodes for the user.
  GET  /app/v1/graph/goals/{goal_id}/tree    — Subtree rooted at a goal (nodes + edges).

Tasks
  GET  /app/v1/graph/tasks                   — List TaskNodes with filters.
  GET  /app/v1/graph/tasks/{task_id}         — Single task + score block + edges.
  POST /app/v1/graph/tasks                   — Create a TaskNode.
  PATCH /app/v1/graph/tasks/{task_id}        — Partial-update a TaskNode.
  DELETE /app/v1/graph/tasks/{task_id}       — Delete a TaskNode.

Resources
  GET  /app/v1/graph/resources               — List ResourceNodes for the user.

Edges
  GET  /app/v1/graph/edges                   — List edges (filterable by type/node).
  POST /app/v1/graph/edges                   — Create a directed edge.
  DELETE /app/v1/graph/edges/{edge_id}       — Delete an edge.

Design Patterns
---------------
- Dependency injection: GraphStore and GraphQueryEngine are injected via
  ``graphclaw.api.deps`` so endpoints never reference concrete backends.
- Cursor pagination: list endpoints accept ``cursor`` and ``limit`` query
  params and return a ``next_cursor`` in the response envelope.
- Transparent dict pass-through: GraphStore returns raw dicts; endpoints
  return them directly so the cockpit receives the full node payload without
  a lossy serialisation layer.

Public API
----------
- router: ``APIRouter`` for /graph routes.

Dependencies
------------
- graphclaw.api.deps: GraphStoreDep, QueryEngineDep, CurrentUserDep.
- graphclaw.models.enums: TaskState, TaskType.
- fastapi: APIRouter, Depends, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, QueryEngineDep
from graphclaw.models.base import generate_task_id, utcnow
from graphclaw.models.enums import TaskState, TaskType

logger = logging.getLogger(__name__)


def _normalize_edge(e: dict) -> dict:
    """Ensure edge dicts have canonical source_id/target_id/edge_type/id fields."""
    result = {k: v for k, v in e.items() if not k.startswith("_")}
    result.setdefault("source_id", e.get("_start_id", ""))
    result.setdefault("target_id", e.get("_end_id", ""))
    result.setdefault("edge_type", e.get("_label", e.get("type", "")))
    result.setdefault("id", e.get("edge_id", ""))
    result.setdefault("edge_id", result["id"])
    return result


router = APIRouter(prefix="/graph", tags=["graph"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class NodeListResponse(BaseModel):
    """Generic paginated node list envelope."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None
    total: int | None = None


class GoalTreeResponse(BaseModel):
    """Subtree rooted at a goal: all descendant nodes and connecting edges."""

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class TaskDetailResponse(BaseModel):
    """Single task with its score block and incident edges."""

    task: dict[str, Any]
    score: dict[str, Any] | None = None
    edges: list[dict[str, Any]]


class CreateTaskRequest(BaseModel):
    """Minimal fields required to create a new TaskNode via the API."""

    task_type: str
    title: str
    description: str = ""
    assignee_id: str | None = None
    deadline: str | None = None
    parent_goal_id: str | None = None
    priority: str | None = None
    tags: list[str] = []


class UpdateTaskRequest(BaseModel):
    """Partial update fields for an existing TaskNode."""

    state: str | None = None
    title: str | None = None
    description: str | None = None
    deadline: str | None = None
    assignee_id: str | None = None
    priority: str | None = None
    agent_override: bool | None = None
    tags: list[str] | None = None


class EdgeListResponse(BaseModel):
    """Paginated edge list envelope."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


class CreateEdgeRequest(BaseModel):
    """Fields required to create a directed edge between two nodes."""

    source_id: str
    target_id: str
    edge_type: str
    metadata: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.get(
    "/goals",
    response_model=NodeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List goal nodes",
    description=(
        "Return all GoalNodes visible to the authenticated user.  "
        "Optionally filter by organisation or goal state."
    ),
)
async def list_goals(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    org_id: str | None = Query(default=None, description="Filter by organisation ID"),
    state: str | None = Query(default=None, description="Filter by GoalState value"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> NodeListResponse:
    """List GoalNodes for the authenticated user."""
    filters: dict[str, Any] = {"owned_by": user_id}
    if org_id:
        filters["org_id"] = org_id
    if state:
        filters["state"] = state

    # Goals are COMPOSITE TaskNodes — query TaskNode label with task_type filter.
    filters["task_type"] = "COMPOSITE"
    nodes = await graph_store.list_nodes("TaskNode", filters)
    # Sort newest first so freshly created goals appear at the top of the list.
    nodes.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    # Simple cursor: treat cursor as an offset index encoded as a string int.
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = nodes[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(nodes) else None

    return NodeListResponse(items=page, next_cursor=next_cursor, total=len(nodes))


@router.get(
    "/goals/{goal_id}/tree",
    response_model=GoalTreeResponse,
    status_code=status.HTTP_200_OK,
    summary="Goal subtree",
    description=(
        "Return all nodes reachable from this goal (tasks, constraints, resources) "
        "and the edges that connect them, up to ``depth`` hops."
    ),
)
async def get_goal_tree(
    goal_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    query_engine: QueryEngineDep,
    depth: int = Query(default=3, ge=1, le=10),
) -> GoalTreeResponse:
    """Return the subtree rooted at *goal_id*."""
    goal = await graph_store.get_node(goal_id)
    if goal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Goal '{goal_id}' not found"
        )

    # Gather tasks belonging to this goal
    task_filters: dict[str, Any] = {"parent_goal_id": goal_id}
    tasks = await graph_store.list_nodes("TaskNode", task_filters)

    # Collect all edges incident to goal + each task
    all_node_ids = [goal_id] + [t["id"] for t in tasks if "id" in t]
    edge_set: list[dict[str, Any]] = []
    seen_edge_ids: set[str] = set()

    for nid in all_node_ids:
        for direction in ("out", "in"):
            edges = await graph_store.get_edges(nid, direction=direction)
            for e in edges:
                eid = e.get("id") or e.get("edge_id") or str(e)
                if eid not in seen_edge_ids:
                    seen_edge_ids.add(eid)
                    edge_set.append(e)

    return GoalTreeResponse(nodes=[goal] + tasks, edges=edge_set)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get(
    "/tasks",
    response_model=NodeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List task nodes",
    description=(
        "Return TaskNodes matching the given filters.  Supports filtering by "
        "state, assignee, organisation, goal, and task type."
    ),
)
async def list_tasks(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    state: str | None = Query(default=None, description="TaskState filter"),
    assignee_id: str | None = Query(default=None),
    org_id: str | None = Query(default=None),
    goal_id: str | None = Query(default=None, alias="goal_id", description="Parent goal filter"),
    task_type: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> NodeListResponse:
    """List TaskNodes for the authenticated user."""
    filters: dict[str, Any] = {"owned_by": user_id}
    if state:
        filters["state"] = state
    if assignee_id:
        filters["assigned_to"] = assignee_id
    if org_id:
        filters["org_id"] = org_id
    if goal_id:
        filters["parent_goal_id"] = goal_id
    if task_type:
        filters["task_type"] = task_type

    nodes = await graph_store.list_nodes("TaskNode", filters)
    # Sort newest first so freshly created tasks appear at the top of the list.
    nodes.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = nodes[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(nodes) else None

    return NodeListResponse(items=page, next_cursor=next_cursor, total=len(nodes))


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task detail",
    description="Return a single TaskNode with its latest score block and all incident edges.",
)
async def get_task(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> TaskDetailResponse:
    """Return task detail for *task_id*."""
    task = await graph_store.get_node(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    # Gather all incident edges (both directions)
    out_edges = await graph_store.get_edges(task_id, direction="out")
    in_edges = await graph_store.get_edges(task_id, direction="in")
    edges = out_edges + in_edges

    # Extract the scoring block that is already embedded in the task node.
    score_raw = task.get("scoring") or task.get("score_block")
    if isinstance(score_raw, str):
        try:
            score: dict | None = json.loads(score_raw)
        except (json.JSONDecodeError, ValueError):
            score = None
    else:
        score = score_raw

    return TaskDetailResponse(task=task, score=score, edges=edges)


@router.post(
    "/tasks",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create task node",
    description="Create a new TaskNode in the graph.",
)
async def create_task(
    body: CreateTaskRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> dict[str, Any]:
    """Create a new TaskNode."""

    from graphclaw.models.nodes import TaskNode

    # Map string task_type to enum; raise 422 if unknown.
    try:
        ttype = TaskType(body.task_type)
    except ValueError:
        valid = [t.value for t in TaskType]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid task_type '{body.task_type}'. Valid values: {valid}",
        )

    # Generate a valid task ID using the canonical generator (TSK-API-NNNN-XXX).
    task_id = generate_task_id("API", ttype)

    node = TaskNode(
        id=task_id,
        created_at=utcnow(),
        updated_at=utcnow(),
        task_type=ttype,
        title=body.title,
        description=body.description,
        owned_by=user_id,
        created_by=user_id,
        assigned_to=body.assignee_id,
        state=TaskState.PENDING,
        tags=body.tags,
    )

    created = await graph_store.create_node(node)

    # Wire relationship edges: task → owner, task → assignee
    try:
        await graph_store.create_edge(task_id, user_id, "OWNED_BY", {})
    except Exception as exc:
        logger.warning("graph: could not wire OWNED_BY edge for task %s: %s", task_id, exc)

    if body.assignee_id:
        try:
            await graph_store.create_edge(task_id, body.assignee_id, "ASSIGNED_TO", {})
        except Exception as exc:
            logger.warning("graph: could not wire ASSIGNED_TO edge for task %s: %s", task_id, exc)

    logger.info("graph: created task %s for user_id=%s", task_id, user_id)
    return created


@router.patch(
    "/tasks/{task_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Update task node",
    description="Partial-update a TaskNode.  Only provided fields are changed.",
)
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> dict[str, Any]:
    """Partial-update a TaskNode."""
    existing = await graph_store.get_node(task_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return existing

    updated = await graph_store.update_node(task_id, updates)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found after update"
        )

    logger.info("graph: updated task %s fields=%s user_id=%s", task_id, list(updates), user_id)
    return updated


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete task node",
    description="Delete a TaskNode and all its incident edges (DETACH DELETE).",
)
async def delete_task(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> None:
    """Delete *task_id* from the graph."""
    existing = await graph_store.get_node(task_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    await graph_store.delete_node(task_id)
    logger.info("graph: deleted task %s by user_id=%s", task_id, user_id)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@router.get(
    "/resources",
    response_model=NodeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List resource nodes",
    description="Return ResourceNodes (people and AI agents) visible to the authenticated user.",
)
async def list_resources(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    org_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> NodeListResponse:
    """List ResourceNodes for the authenticated user."""
    filters: dict[str, Any] = {}
    if org_id:
        filters["org_id"] = org_id

    nodes = await graph_store.list_nodes("Resource", filters)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = nodes[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(nodes) else None

    return NodeListResponse(items=page, next_cursor=next_cursor, total=len(nodes))


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


@router.get(
    "/edges",
    response_model=EdgeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List edges",
    description=("Return graph edges, optionally filtered by type, source node, or target node."),
)
async def list_edges(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    edge_type: str | None = Query(default=None, description="Edge type (e.g. DEPENDS_ON, BLOCKS)"),
    source_id: str | None = Query(default=None, description="Source node ID"),
    target_id: str | None = Query(default=None, description="Target node ID"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> EdgeListResponse:
    """List edges matching the given filters."""
    anchor_id = source_id or target_id
    direction = "out" if source_id else ("in" if target_id else "out")

    if anchor_id:
        raw_edges = await graph_store.get_edges(anchor_id, direction=direction, edge_type=edge_type)
    else:
        # No anchor — return edges incident to any task owned by this user.
        tasks = await graph_store.list_nodes("TaskNode", {"owned_by": user_id})
        seen: set[str] = set()
        raw_edges = []
        for task in tasks[:20]:  # Cap at 20 tasks to avoid N+1 explosion
            tid = task.get("id", "")
            if not tid:
                continue
            for e in await graph_store.get_edges(tid, direction="out"):
                eid = e.get("id") or e.get("edge_id") or str(e)
                if eid not in seen:
                    seen.add(eid)
                    raw_edges.append(e)

    edges = [_normalize_edge(e) for e in raw_edges]

    if edge_type:
        edges = [e for e in edges if e.get("edge_type") == edge_type]

    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = edges[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(edges) else None

    return EdgeListResponse(items=page, next_cursor=next_cursor)


@router.post(
    "/edges",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create edge",
    description="Create a directed edge between two nodes.",
)
async def create_edge(
    body: CreateEdgeRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> dict[str, Any]:
    """Create a directed edge from ``source_id`` to ``target_id``."""
    # Validate both nodes exist.
    src = await graph_store.get_node(body.source_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source node '{body.source_id}' not found",
        )
    tgt = await graph_store.get_node(body.target_id)
    if tgt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target node '{body.target_id}' not found",
        )

    edge_id = f"EDGE-{uuid4().hex[:12]}"
    metadata = dict(body.metadata or {})
    metadata.update(
        {
            "id": edge_id,
            "edge_id": edge_id,
            "source_id": body.source_id,
            "target_id": body.target_id,
            "edge_type": body.edge_type,
        }
    )

    edge = await graph_store.create_edge(
        body.source_id,
        body.target_id,
        body.edge_type,
        metadata,
    )
    # Ensure the response always contains useful identity fields
    response = _normalize_edge(edge)
    if not response.get("id"):
        response["id"] = edge_id
        response["edge_id"] = edge_id
        response["source_id"] = body.source_id
        response["target_id"] = body.target_id
        response["edge_type"] = body.edge_type
    logger.info(
        "graph: created edge %s->%s type=%s user_id=%s",
        body.source_id,
        body.target_id,
        body.edge_type,
        user_id,
    )
    return response


@router.delete(
    "/edges/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edge",
    description="Delete an edge by its ID.",
)
async def delete_edge(
    edge_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> None:
    """Delete the edge with the given *edge_id*."""
    # edge_id is a string property (e.g. EDGE-abc123), not the AGE internal int id
    if hasattr(graph_store, "delete_edge_by_property"):
        await graph_store.delete_edge_by_property("id", edge_id)
    else:
        # Fallback: use the repository's delete_edge expecting a numeric id
        # (works if edge_id is numeric, otherwise silently no-ops)
        try:
            await graph_store.delete_edge(edge_id)
        except (ValueError, Exception):
            pass
    logger.info("graph: deleted edge %s by user_id=%s", edge_id, user_id)
