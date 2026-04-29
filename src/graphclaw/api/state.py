"""graphclaw.api.state — State machine endpoints for the cockpit task views.

Description
-----------
Exposes the task state machine to the cockpit UI (PRD §12, §02), allowing the
user to inspect state history, query valid transitions, and manually drive tasks
through the state machine directly from the interface.

Endpoints
---------
  GET  /app/v1/tasks/{task_id}/state-history     — Full transition history log.
  GET  /app/v1/tasks/{task_id}/valid-transitions  — States reachable from current state.
  POST /app/v1/tasks/{task_id}/transition         — Apply a human-initiated transition.

Design Patterns
---------------
- State machine as service: ``StateMachine`` is injected per-request (it holds
  no mutable state between calls) so endpoints are safe to call concurrently.
- Persist after transition: The endpoint persists the updated node dict back to
  the ``GraphStore`` after ``StateMachine.transition()`` mutates ``TaskNode``.
- History from node: ``StateHistoryEntry`` records live on ``TaskNode`` itself.

Public API
----------
- router: ``APIRouter`` for task-level /tasks routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, GraphStoreDep, StateMachineDep.
- graphclaw.models.enums: ChangedBy, TaskState.
- graphclaw.models.nodes: TaskNode.
- graphclaw.state.cascade: persist_transition_and_cascade.
- graphclaw.state.transitions: VALID_TRANSITIONS, InvalidTransitionError.
- fastapi: APIRouter, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, StateMachineDep
from graphclaw.models.deserialization import deserialize_task_node_props
from graphclaw.models.enums import ChangedBy, TaskState
from graphclaw.models.nodes import TaskNode
from graphclaw.state.cascade import persist_transition_and_cascade
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["state"])


def _edge_target_id(edge: dict[str, Any]) -> str:
    """Return a best-effort target node id from a repository edge payload."""
    return str(edge.get("_end_id") or edge.get("target_id") or edge.get("to_id") or "")


async def _is_transition_authorized(
    task_id: str,
    user_id: str,
    raw_task: dict[str, Any],
    graph_store: Any,
) -> bool:
    """Return True when the caller can transition the target task.

    Authorization policy:
    - direct owner via ``owned_by`` field
    - direct assignee via ``assigned_to`` field
    - owner via graph edge ``(task)-[:OWNED_BY]->(user)``
    """
    if str(raw_task.get("owned_by") or "") == user_id:
        return True

    if str(raw_task.get("assigned_to") or "") == user_id:
        return True

    try:
        owner_edges = await graph_store.get_edges(task_id, direction="out", edge_type="OWNED_BY")
    except Exception as exc:  # noqa: BLE001
        logger.debug("state: ownership edge lookup failed for task %s: %s", task_id, exc)
        owner_edges = []

    for edge in owner_edges:
        if _edge_target_id(edge) == user_id:
            return True

    return False


def _deserialize_task_fields(raw: dict) -> dict:
    """Parse JSON-string fields in a task dict from AGE back to native types."""
    return deserialize_task_node_props(raw)


class StateHistoryResponse(BaseModel):
    """Paginated state history response."""

    entries: list[dict[str, Any]]
    next_cursor: str | None = None


class ValidTransitionsResponse(BaseModel):
    """Valid target states reachable from the task's current state."""

    current_state: str
    valid_states: list[str]


class TransitionRequest(BaseModel):
    """Request body for a manual state transition."""

    target_state: str
    reason: str | None = None


@router.get(
    "/{task_id}/state-history",
    response_model=StateHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task state history",
    description=(
        "Return the full audit trail of state transitions for the given task, "
        "ordered oldest-first. Each entry records from/to states, timestamp, "
        "who changed it, and an optional reason."
    ),
)
async def get_state_history(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> StateHistoryResponse:
    """Return state transition history for *task_id*."""
    task = await graph_store.get_node(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    task = _deserialize_task_fields(task)
    history: list[dict[str, Any]] = task.get("state_history") or []
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = history[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(history) else None

    return StateHistoryResponse(entries=page, next_cursor=next_cursor)


@router.get(
    "/{task_id}/valid-transitions",
    response_model=ValidTransitionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get valid state transitions",
    description=(
        "Return the list of target states the task can transition to from its "
        "current state, according to the transition table and domain guards."
    ),
)
async def get_valid_transitions(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> ValidTransitionsResponse:
    """Return valid next states for *task_id*."""
    task = await graph_store.get_node(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    current_raw: str = task.get("state", "PENDING")
    try:
        current_state = TaskState(current_raw)
    except ValueError:
        current_state = TaskState.PENDING

    valid: list[TaskState] = VALID_TRANSITIONS.get(current_state, [])
    return ValidTransitionsResponse(
        current_state=current_state.value,
        valid_states=[s.value for s in valid],
    )


@router.post(
    "/{task_id}/transition",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Transition task state",
    description=(
        "Apply a human-initiated state transition to the task. "
        "The transition is validated against the state machine rules; "
        "invalid transitions return HTTP 422."
    ),
)
async def transition_task(
    task_id: str,
    body: TransitionRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    state_machine: StateMachineDep,
) -> dict[str, Any]:
    """Drive *task_id* to ``body.target_state``."""
    raw_task = await graph_store.get_node(task_id)
    if raw_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    if not await _is_transition_authorized(task_id, user_id, raw_task, graph_store):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{user_id}' is not authorized to transition task '{task_id}'",
        )

    try:
        target_state = TaskState(body.target_state)
    except ValueError:
        valid_values = [s.value for s in TaskState]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid target_state '{body.target_state}'. Valid values: {valid_values}",
        )

    try:
        task_node = TaskNode.model_validate(_deserialize_task_fields(raw_task))
    except Exception as exc:
        logger.error("state: failed to deserialise task %s: %s", task_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load task '{task_id}': {exc}",
        )

    old_state = task_node.state.value
    try:
        await persist_transition_and_cascade(
            task_node,
            target_state,
            ChangedBy.HUMAN,
            body.reason or "",
            graph_store,
            state_machine,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info(
        "state: task %s transitioned %s -> %s by user_id=%s reason=%r",
        task_id,
        old_state,
        task_node.state.value,
        user_id,
        body.reason,
    )

    return {
        "task_id": task_node.id,
        "old_state": old_state,
        "new_state": task_node.state.value,
        "status": "transitioned",
    }
