"""graphclaw.api.agent_activity — Historical activity feed endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.agent.activity_formatter import format_event
from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.storage.minio_log_reader import MinioLogReader, parse_record_timestamp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["app-api"])

ActivityType = Literal["all", "decisions", "comms", "skills", "errors"]

_DECISION_EVENTS = {"task.scored", "task.state_changed", "briefing.ready", "agent.scoring_cycle"}
_COMMS_EVENTS = {"inbound.processed", "agent.message", "outbound.sent"}
_SKILL_EVENTS = {"skill.completed", "agent.tool_call", "mcp.tool_call", "heartbeat.failed"}


class ActivityItem(BaseModel):
    timestamp: datetime
    event_type: str
    message: str
    task_id: str | None = None
    status: str | None = None
    session_id: str | None = None
    raw: dict[str, Any] | None = None


class ActivityResponse(BaseModel):
    items: list[ActivityItem]
    next_cursor: str | None = None


class ErrorResponse(BaseModel):
    error: str
    max_days: int | None = None


def _read_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_failed(record: dict[str, Any]) -> bool:
    status_value = (_read_str(record, "status") or "").upper()
    level_value = (_read_str(record, "level") or "").upper()
    event_type = (_read_str(record, "event_type") or "").lower()
    return (
        status_value in {"FAILED", "ERROR", "TIMEOUT"}
        or level_value in {"ERROR", "CRITICAL"}
        or "failed" in event_type
        or "error" in event_type
    )


def _matches_type_filter(record: dict[str, Any], activity_type: ActivityType) -> bool:
    if activity_type == "all":
        return True

    event_type = _read_str(record, "event_type") or ""

    if activity_type == "decisions":
        return event_type in _DECISION_EVENTS
    if activity_type == "comms":
        return event_type in _COMMS_EVENTS
    if activity_type == "skills":
        return event_type in _SKILL_EVENTS
    if activity_type == "errors":
        return _is_failed(record)

    return False


def _infer_status(record: dict[str, Any]) -> str:
    if _is_failed(record):
        return "failed"

    event_type = _read_str(record, "event_type") or ""
    if event_type == "task.scored":
        return "done"
    if event_type == "agent.message" or event_type == "outbound.sent":
        return "sent"
    if event_type == "inbound.processed":
        return "matched"
    if event_type == "task.state_changed":
        return "running"
    if event_type == "briefing.ready":
        return "trigger"

    return "done"


@router.get(
    "/activity",
    response_model=ActivityResponse,
    status_code=status.HTTP_200_OK,
    summary="Get historical agent activity",
    description=(
        "Return chronological activity records from MinIO NDJSON logs for the authenticated user. "
        "Supports time-range filters, event-type filters, and cursor pagination."
    ),
    responses={
        400: {
            "description": "Invalid query range/cursor",
            "model": ErrorResponse,
        }
    },
)
async def get_agent_activity(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    from_dt: datetime = Query(alias="from"),
    to_dt: datetime = Query(alias="to"),
    activity_type: ActivityType = Query(default="all", alias="type"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ActivityResponse:
    """List paginated activity rows for the authenticated user."""
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    else:
        from_dt = from_dt.astimezone(UTC)

    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)
    else:
        to_dt = to_dt.astimezone(UTC)

    if to_dt <= from_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_range"},
        )

    if to_dt - from_dt > timedelta(days=7):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "range_too_large", "max_days": 7},
        )

    reader = MinioLogReader(storage_client=storage_client, max_files_per_request=50)

    try:
        records, next_cursor = await reader.read_page(
            user_id=user_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            cursor=cursor,
            include_record=lambda record: _matches_type_filter(record, activity_type),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_cursor"},
        ) from exc

    items: list[ActivityItem] = []
    for record in records:
        timestamp = parse_record_timestamp(record)
        if timestamp is None:
            logger.warning("agent_activity: skipping record with invalid timestamp")
            continue

        event_type = _read_str(record, "event_type") or "event"
        items.append(
            ActivityItem(
                timestamp=timestamp,
                event_type=event_type,
                message=format_event(record),
                task_id=_read_str(record, "task_id", "taskId", "node_id", "nodeId"),
                status=_infer_status(record),
                session_id=_read_str(record, "session_id", "sessionId"),
                raw=record,
            )
        )

    return ActivityResponse(items=items, next_cursor=next_cursor)
