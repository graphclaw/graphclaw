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
- Persist after transition: The endpoint is responsible for persisting the
  updated node dict back to the ``GraphStore`` after ``StateMachine.transition()``
  mutates the in-memory ``TaskNode``.
- History from node: ``StateHistoryEntry`` records live on the ``TaskNode``
  itself (``task.state_history``), keeping the history co-located with the node.

Public API
----------
- router: ``APIRouter`` for task-level /tasks routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, GraphStoreDep, StateMachineDep.
- graphclaw.models.enums: ChangedBy, TaskState.
- graphclaw.models.nodes: TaskNode, StateHistoryEntry.
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
from graphclaw.models.enums import ChangedBy, TaskState
from graphclaw.models.nodes import TaskNode
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError

logger = logging.getLogger(__name__)

# The state endpoints live at /tasks/... (no /graph prefix) per the API contract.
router = APIRouter(prefix="/tasks", tags=["state"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{task_id}/state-history",
    response_model=StateHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task state history",
    description=(
        "Return the full audit trail of state transitions for the given task, "
        "ordered oldest-first.  Each entry records from/to states, timestamp, "
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
        "Apply a human-initiated state transition to the task.  "
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

    # Validate target state string.
    try:
        target_state = TaskState(body.target_state)
    except ValueError:
        valid_values = [s.value for s in TaskState]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid target_state '{body.target_state}'. Valid values: {valid_values}",
        )

    # Deserialise the dict back to a TaskNode so the state machine can operate.
    try:
        task_node = TaskNode.model_validate(raw_task)
    except Exception as exc:
        logger.error("state: failed to deserialise task %s: %s", task_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load task '{task_id}': {exc}",
        )

    # Apply the transition via the StateMachine (mutates task_node in place).
    try:
        state_machine.transition(
            task_node,
            target_state,
            ChangedBy.HUMAN,
            body.reason or "",
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Persist the updated node back to the graph store.
    updated_dict = task_node.model_dump(mode="json")
    persisted = await graph_store.update_node(task_id, updated_dict)
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transition applied but failed to persist task '{task_id}'",
        )

    logger.info(
        "state: task %s transitioned to %s by user_id=%s reason=%r",
        task_id,
        target_state.value,
        user_id,
        body.reason,
    )
    return persisted
