# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.agent_activity — Historical activity feed endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.agent.activity_formatter import format_event
from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.storage.minio_log_reader import (
    MinioLogReader,
    parse_hour_from_key,
    parse_record_timestamp,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc

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


class AgentSessionItem(BaseModel):
    sessionId: str
    startedAt: datetime
    completedAt: datetime
    triggerType: str
    toolCallCount: int = 0
    skillCount: int = 0
    messagesSent: int = 0
    messagesReceived: int = 0
    inputTokens: int = 0
    outputTokens: int = 0
    status: str = "completed"


class AgentSessionsResponse(BaseModel):
    items: list[AgentSessionItem]
    nextCursor: int | None = None


def _read_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_int(record: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = int(float(value.strip()))
            except ValueError:
                continue
            return parsed
    return 0


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


def _infer_trigger_type(record: dict[str, Any]) -> str:
    trigger_type = (_read_str(record, "trigger_type", "triggerType") or "").lower()
    if trigger_type in {"scheduled", "time_based", "timer"}:
        return "scheduled"
    if trigger_type in {"on_demand", "manual"}:
        return "manual"
    if trigger_type in {"inbound", "event_based", "event"}:
        return "event"

    source = (_read_str(record, "trigger_source", "triggerSource") or "").lower()
    if source in {"heartbeat", "scheduled"}:
        return "scheduled"
    if source in {"on_demand", "manual"}:
        return "manual"
    if source in {"inbound", "event", "property_change"}:
        return "event"

    event_type = (_read_str(record, "event_type") or "").lower()
    if event_type in {"inbound.processed", "agent.message", "outbound.sent"}:
        return "event"

    return "scheduled"


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


@router.get(
    "/sessions",
    response_model=AgentSessionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get agent sessions",
    description=(
        "Return session summaries aggregated from MinIO NDJSON logs for the "
        "authenticated user with offset pagination."
    ),
)
async def get_agent_sessions(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=10, ge=1, le=50),
    cursor: int = Query(default=0, ge=0),
) -> AgentSessionsResponse:
    """List aggregated agent sessions for the authenticated user."""
    now = datetime.now(tz=UTC)

    if to_dt is None:
        to_dt = now
    elif to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)
    else:
        to_dt = to_dt.astimezone(UTC)

    if from_dt is None:
        from_dt = to_dt - timedelta(days=7)
    elif from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    else:
        from_dt = from_dt.astimezone(UTC)

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

    all_keys = await storage_client.list_objects(f"{user_id}/logs/")
    earliest_hour = from_dt.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)

    scored: list[tuple[datetime, str]] = []
    for key in all_keys:
        hour_dt = parse_hour_from_key(key)
        if hour_dt is None:
            continue
        if hour_dt > to_dt or hour_dt < earliest_hour:
            continue
        scored.append((hour_dt, key))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    file_keys = [key for _, key in scored[:50]]

    grouped: dict[str, dict[str, Any]] = {}
    for file_key in file_keys:
        raw = await storage_client.read(file_key)
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            timestamp = parse_record_timestamp(record)
            if timestamp is None or timestamp < from_dt or timestamp >= to_dt:
                continue

            session_id = _read_str(record, "session_id", "sessionId")
            if not session_id:
                continue

            bucket = grouped.get(session_id)
            if bucket is None:
                bucket = {
                    "sessionId": session_id,
                    "startedAt": timestamp,
                    "completedAt": timestamp,
                    "triggerType": _infer_trigger_type(record),
                    "toolCallCount": 0,
                    "skillCount": 0,
                    "messagesSent": 0,
                    "messagesReceived": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "status": "completed",
                }
                grouped[session_id] = bucket
            else:
                if timestamp < bucket["startedAt"]:
                    bucket["startedAt"] = timestamp
                if timestamp > bucket["completedAt"]:
                    bucket["completedAt"] = timestamp

            event_type = (_read_str(record, "event_type") or "").lower()
            if event_type in {"agent.tool_call", "mcp.tool_call"}:
                bucket["toolCallCount"] += 1
            if event_type == "skill.completed":
                bucket["skillCount"] += 1
            if event_type in {"agent.message", "outbound.sent"}:
                bucket["messagesSent"] += 1
            if event_type == "inbound.processed":
                bucket["messagesReceived"] += 1

            bucket["inputTokens"] += _read_int(
                record, "input_tokens", "inputTokens", "prompt_tokens"
            )
            bucket["outputTokens"] += _read_int(
                record, "output_tokens", "outputTokens", "completion_tokens"
            )

            status_value = (_read_str(record, "status") or "").upper()
            if status_value in {"FAILED", "ERROR", "TIMEOUT", "BLOCKED"} or _is_failed(record):
                bucket["status"] = "failed"

    sessions = [
        AgentSessionItem(
            sessionId=entry["sessionId"],
            startedAt=entry["startedAt"],
            completedAt=entry["completedAt"],
            triggerType=entry["triggerType"],
            toolCallCount=entry["toolCallCount"],
            skillCount=entry["skillCount"],
            messagesSent=entry["messagesSent"],
            messagesReceived=entry["messagesReceived"],
            inputTokens=entry["inputTokens"],
            outputTokens=entry["outputTokens"],
            status=entry["status"],
        )
        for entry in grouped.values()
    ]
    sessions.sort(key=lambda item: item.startedAt, reverse=True)

    start = max(0, cursor)
    end = start + limit
    page = sessions[start:end]
    next_cursor = end if end < len(sessions) else None
    return AgentSessionsResponse(items=page, nextCursor=next_cursor)
