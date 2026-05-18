# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
GC-U-API-W50-004 - validates inbound and outbound communication log APIs.

Scenario: Tasks comms log routes should return paginated user-scoped inbound and
outbound entries from NDJSON logs and reject invalid ranges.

PRD: docs/cockpit-backend-api-prd.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-05

Cases covered:
- inbound log returns newest-first records with opaque cursor pagination
- outbound log returns outbound.sent rows with normalized display fields
- tasks comms logs reject invalid from/to ranges
- tasks comms logs enforce authentication

Notes:
- Uses FakeStorageClient with in-memory JSONL fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-task-logs-001"


def _make_app(storage: FakeStorageClient) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    app.dependency_overrides[require_auth] = _fake_auth
    app.state.storage_client = storage
    return app


def _write_log_file(storage: FakeStorageClient, key: str, records: list[dict]) -> None:
    payload = "\n".join(json.dumps(record) for record in records) + "\n"
    storage._data[key] = payload.encode("utf-8")


def test_inbound_log_returns_cursor_paginated_rows() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-05/1200Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-05T12:05:00Z",
                "event_type": "inbound.processed",
                "channel": "email",
                "sender": "sarah@example.com",
                "body_summary": "Budget approved.",
                "task_id": "TK-100",
                "action": "state_update_published",
                "signal": "DONE",
            },
            {
                "timestamp": "2026-05-05T12:15:00Z",
                "event_type": "inbound.processed",
                "channel": "email",
                "sender": "alex@example.com",
                "body_summary": "Need one more revision.",
                "task_id": "TK-101",
                "action": "manual_match_required",
                "signal": "BLOCKED",
            },
        ],
    )

    client = TestClient(_make_app(storage))

    first = client.get(
        "/app/v1/tasks/inbound-log",
        params={
            "from": "2026-05-05T12:00:00Z",
            "to": "2026-05-05T13:00:00Z",
            "limit": 1,
        },
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["taskId"] == "TK-101"
    assert first_payload["items"][0]["actionTaken"] == "manual_match_required"
    assert first_payload["nextCursor"] is not None

    second = client.get(
        "/app/v1/tasks/inbound-log",
        params={
            "from": "2026-05-05T12:00:00Z",
            "to": "2026-05-05T13:00:00Z",
            "limit": 1,
            "cursor": first_payload["nextCursor"],
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["taskId"] == "TK-100"
    assert second_payload["nextCursor"] is None


def test_outbound_log_returns_sent_rows() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-05/1400Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-05T14:04:00Z",
                "event_type": "outbound.sent",
                "channel": "email",
                "to_display": "Alex M.",
                "subject": "Quick update",
                "summary": "Can you confirm by EOD?",
                "task_id": "TK-777",
                "status": "sent",
            },
            {
                "timestamp": "2026-05-05T14:03:00Z",
                "event_type": "inbound.processed",
                "channel": "email",
            },
        ],
    )

    client = TestClient(_make_app(storage))
    response = client.get(
        "/app/v1/tasks/outbound-log",
        params={
            "from": "2026-05-05T14:00:00Z",
            "to": "2026-05-05T15:00:00Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["channel"] == "email"
    assert row["toDisplay"] == "Alex M."
    assert row["taskId"] == "TK-777"
    assert row["status"] == "sent"


def test_tasks_log_routes_reject_invalid_range() -> None:
    client = TestClient(_make_app(FakeStorageClient()))

    inbound = client.get(
        "/app/v1/tasks/inbound-log",
        params={
            "from": "2026-05-05T15:00:00Z",
            "to": "2026-05-05T14:00:00Z",
        },
    )
    assert inbound.status_code == 400

    outbound = client.get(
        "/app/v1/tasks/outbound-log",
        params={
            "from": "2026-05-05T15:00:00Z",
            "to": "2026-05-05T14:00:00Z",
        },
    )
    assert outbound.status_code == 400


def test_tasks_log_routes_require_auth() -> None:
    app = FastAPI()
    app.include_router(app_router)
    app.state.storage_client = FakeStorageClient()

    client = TestClient(app, raise_server_exceptions=False)

    inbound = client.get(
        "/app/v1/tasks/inbound-log",
        params={
            "from": datetime(2026, 5, 5, 12, 0, tzinfo=UTC).isoformat(),
            "to": datetime(2026, 5, 5, 13, 0, tzinfo=UTC).isoformat(),
        },
    )
    assert inbound.status_code in (401, 403)

    outbound = client.get(
        "/app/v1/tasks/outbound-log",
        params={
            "from": datetime(2026, 5, 5, 12, 0, tzinfo=UTC).isoformat(),
            "to": datetime(2026, 5, 5, 13, 0, tzinfo=UTC).isoformat(),
        },
    )
    assert outbound.status_code in (401, 403)
