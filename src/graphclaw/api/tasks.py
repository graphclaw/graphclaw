# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.tasks — Inbound/outbound communication log endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.storage.minio_log_reader import MinioLogReader, parse_record_timestamp

UTC = timezone.utc

router = APIRouter(prefix="/tasks", tags=["app-api"])


class ErrorResponse(BaseModel):
    error: str


class InboundLogItem(BaseModel):
    timestamp: datetime
    channel: str
    fromDisplay: str | None = None
    messagePreview: str | None = None
    taskId: str | None = None
    taskTitle: str | None = None
    actionTaken: str | None = None
    signal: str | None = None


class InboundLogResponse(BaseModel):
    items: list[InboundLogItem]
    nextCursor: str | None = None


class OutboundLogItem(BaseModel):
    timestamp: datetime
    channel: str
    toDisplay: str | None = None
    subject: str | None = None
    summary: str | None = None
    taskId: str | None = None
    taskTitle: str | None = None
    status: str | None = None


class OutboundLogResponse(BaseModel):
    items: list[OutboundLogItem]
    nextCursor: str | None = None


def _read_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_bounds(from_dt: datetime, to_dt: datetime) -> tuple[datetime, datetime]:
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

    return from_dt, to_dt


def _normalize_event_type(record: dict[str, Any]) -> str:
    return (_read_str(record, "event_type") or "").lower()


@router.get(
    "/inbound-log",
    response_model=InboundLogResponse,
    status_code=status.HTTP_200_OK,
    summary="Get inbound communication log",
    responses={400: {"model": ErrorResponse}},
)
async def get_inbound_log(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    from_dt: datetime = Query(alias="from"),
    to_dt: datetime = Query(alias="to"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> InboundLogResponse:
    """Return paginated inbound communication records for a user."""
    from_dt, to_dt = _normalize_bounds(from_dt, to_dt)
    reader = MinioLogReader(storage_client=storage_client, max_files_per_request=50)

    try:
        records, next_cursor = await reader.read_page(
            user_id=user_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            cursor=cursor,
            include_record=lambda record: _normalize_event_type(record) == "inbound.processed",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_cursor"},
        ) from exc

    items: list[InboundLogItem] = []
    for record in records:
        timestamp = parse_record_timestamp(record)
        if timestamp is None:
            continue

        items.append(
            InboundLogItem(
                timestamp=timestamp,
                channel=_read_str(record, "channel", "channel_type", "channelType") or "unknown",
                fromDisplay=_read_str(record, "from_display", "fromDisplay", "sender", "from"),
                messagePreview=_read_str(
                    record,
                    "message_preview",
                    "messagePreview",
                    "body_summary",
                    "summary",
                    "subject",
                ),
                taskId=_read_str(record, "task_id", "taskId", "task_id_matched"),
                taskTitle=_read_str(record, "task_title", "taskTitle"),
                actionTaken=_read_str(record, "action_taken", "actionTaken", "action"),
                signal=_read_str(record, "signal", "status_signal", "statusSignal"),
            )
        )

    return InboundLogResponse(items=items, nextCursor=next_cursor)


@router.get(
    "/outbound-log",
    response_model=OutboundLogResponse,
    status_code=status.HTTP_200_OK,
    summary="Get outbound communication log",
    responses={400: {"model": ErrorResponse}},
)
async def get_outbound_log(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    from_dt: datetime = Query(alias="from"),
    to_dt: datetime = Query(alias="to"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> OutboundLogResponse:
    """Return paginated outbound communication records for a user."""
    from_dt, to_dt = _normalize_bounds(from_dt, to_dt)
    reader = MinioLogReader(storage_client=storage_client, max_files_per_request=50)

    try:
        records, next_cursor = await reader.read_page(
            user_id=user_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            cursor=cursor,
            include_record=lambda record: _normalize_event_type(record) == "outbound.sent",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_cursor"},
        ) from exc

    items: list[OutboundLogItem] = []
    for record in records:
        timestamp = parse_record_timestamp(record)
        if timestamp is None:
            continue

        items.append(
            OutboundLogItem(
                timestamp=timestamp,
                channel=_read_str(record, "channel", "channel_type", "channelType") or "unknown",
                toDisplay=_read_str(
                    record,
                    "to_display",
                    "toDisplay",
                    "to",
                    "recipient_display",
                    "recipient_hashed",
                ),
                subject=_read_str(record, "subject"),
                summary=_read_str(record, "summary", "message", "result"),
                taskId=_read_str(record, "task_id", "taskId"),
                taskTitle=_read_str(record, "task_title", "taskTitle"),
                status=_read_str(record, "status") or "sent",
            )
        )

    return OutboundLogResponse(items=items, nextCursor=next_cursor)
