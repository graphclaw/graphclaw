# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.storage.minio_log_reader — MinIO NDJSON activity log reader."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from graphclaw.infra.storage import StorageClient

_HOUR_KEY_RE = re.compile(r"/(?P<date>\d{4}-\d{2}-\d{2})/(?P<hour>\d{2})00Z")


@dataclass(frozen=True)
class PageCursor:
    file_key: str
    line_offset: int


class MinioLogReader:
    """Read reverse-chronological activity records from NDJSON logs."""

    def __init__(self, storage_client: StorageClient, max_files_per_request: int = 50) -> None:
        self._storage = storage_client
        self._max_files_per_request = max_files_per_request

    async def read_page(
        self,
        *,
        user_id: str,
        from_dt: datetime,
        to_dt: datetime,
        limit: int,
        cursor: str | None,
        include_record: Callable[[dict[str, Any]], bool],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return one page of activity records plus an opaque next cursor."""
        file_keys = await self._list_candidate_files(user_id=user_id, from_dt=from_dt, to_dt=to_dt)

        start_cursor: PageCursor | None = None
        if cursor:
            start_cursor = decode_cursor(cursor)
            if start_cursor.file_key not in file_keys:
                raise ValueError("Cursor file key is outside the current query window")

        items: list[dict[str, Any]] = []
        cursor_file_found = start_cursor is None

        for file_key in file_keys:
            if start_cursor and not cursor_file_found:
                if file_key != start_cursor.file_key:
                    continue
                cursor_file_found = True

            raw = await self._storage.read(file_key)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            lines.reverse()

            line_offset = 0
            if start_cursor and file_key == start_cursor.file_key:
                line_offset = max(0, start_cursor.line_offset)

            for index in range(line_offset, len(lines)):
                line = lines[index].strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(record, dict):
                    continue

                timestamp = parse_record_timestamp(record)
                if timestamp is None:
                    continue

                if timestamp < from_dt or timestamp >= to_dt:
                    continue

                if not include_record(record):
                    continue

                items.append(record)
                if len(items) >= limit:
                    next_cursor = encode_cursor(
                        PageCursor(file_key=file_key, line_offset=index + 1)
                    )
                    return items, next_cursor

        return items, None

    async def _list_candidate_files(
        self,
        *,
        user_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[str]:
        """List likely relevant NDJSON keys ordered newest-first."""
        prefix = f"{user_id}/logs/"
        keys = await self._storage.list_objects(prefix)

        earliest_hour = from_dt.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)

        scored: list[tuple[datetime, str]] = []
        for key in keys:
            hour_dt = parse_hour_from_key(key)
            if hour_dt is None:
                continue
            if hour_dt > to_dt or hour_dt < earliest_hour:
                continue
            scored.append((hour_dt, key))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [key for _, key in scored[: self._max_files_per_request]]


def encode_cursor(cursor: PageCursor) -> str:
    payload = {"file_key": cursor.file_key, "line_offset": cursor.line_offset}
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decode_cursor(cursor: str) -> PageCursor:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
        file_key = data.get("file_key")
        line_offset = data.get("line_offset")
        if not isinstance(file_key, str) or not isinstance(line_offset, int):
            raise ValueError("Cursor payload is invalid")
        if line_offset < 0:
            raise ValueError("Cursor line offset cannot be negative")
        return PageCursor(file_key=file_key, line_offset=line_offset)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise ValueError("Cursor is not valid base64 JSON") from exc


def parse_hour_from_key(key: str) -> datetime | None:
    match = _HOUR_KEY_RE.search(key)
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group('date')} {match.group('hour')}", "%Y-%m-%d %H"
        ).replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_record_timestamp(record: dict[str, Any]) -> datetime | None:
    for field in ("timestamp", "created_at", "completed_at", "occurred_at"):
        raw = record.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue

        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
        return parsed

    return None
