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

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, StateMachineDep
from graphclaw.models.enums import ChangedBy, TaskState
from graphclaw.models.nodes import TaskNode
from graphclaw.state.cascade import persist_transition_and_cascade
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["state"])

_JSON_STR_FIELDS = ("scoring", "timeline", "progress", "override", "autonomy", "type_metadata")
_JSON_LIST_FIELDS = ("state_history", "update_log", "tags")


def _deserialize_task_fields(raw: dict) -> dict:
    """Parse JSON-string fields in a task dict from AGE back to native types."""
    result = dict(raw)
    for field in _JSON_STR_FIELDS:
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, ValueError):
                result[field] = None
    for field in _JSON_LIST_FIELDS:
        val = result.get(field)
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                result[field] = []
        elif isinstance(val, list):
            parsed_items = []
            for item in val:
                if isinstance(item, str):
                    try:
                        parsed_items.append(json.loads(item))
                    except (json.JSONDecodeError, ValueError):
                        parsed_items.append(item)
                else:
                    parsed_items.append(item)
            result[field] = parsed_items
    return result


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
