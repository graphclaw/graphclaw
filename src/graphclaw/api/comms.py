# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.comms — Communication summary endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.storage.minio_log_reader import parse_hour_from_key, parse_record_timestamp

UTC = timezone.utc

router = APIRouter(prefix="/comms", tags=["app-api"])


class CommsSummaryResponse(BaseModel):
    date: str
    received: int
    sent: int
    matched: int
    unmatched: int


def _read_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@router.get(
    "/summary",
    response_model=CommsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get communications summary",
    description=(
        "Return per-day inbound/outbound communication counters for the authenticated "
        "user by scanning historical NDJSON logs in object storage."
    ),
)
async def get_comms_summary(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    summary_date: date | None = Query(default=None, alias="date"),
) -> CommsSummaryResponse:
    """Aggregate daily communication counts for the authenticated user."""
    target_date = summary_date or datetime.now(tz=UTC).date()
    from_dt = datetime.combine(target_date, time.min, tzinfo=UTC)
    to_dt = from_dt + timedelta(days=1)

    all_keys = await storage_client.list_objects(f"{user_id}/logs/")
    earliest_hour = from_dt - timedelta(hours=1)

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

    received = 0
    sent = 0
    matched = 0
    unmatched = 0

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

            event_type = (_read_str(record, "event_type") or "").lower()

            if event_type == "inbound.processed":
                received += 1
                action = (_read_str(record, "action", "action_taken", "actionTaken") or "").lower()
                if action in {"unmatched", "manual_match_required"}:
                    unmatched += 1
                else:
                    matched += 1
                continue

            if event_type in {"outbound.sent", "agent.message"}:
                sent += 1

    return CommsSummaryResponse(
        date=target_date.isoformat(),
        received=received,
        sent=sent,
        matched=matched,
        unmatched=unmatched,
    )
