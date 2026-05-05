"""
GC-U-API-W50-002 - validates historical activity and session summary APIs.

Scenario: The activity API should return correctly filtered and paginated log
events, and the sessions API should aggregate user-scoped session summaries from
the same NDJSON log source.

PRD: docs/cockpit-backend-api-prd.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-05

Cases covered:
- agent activity returns newest-first records with opaque cursor pagination
- agent activity filters failed/error events for type=errors
- agent activity rejects ranges greater than seven days
- agent activity enforces authentication
- agent sessions aggregates counts and tokens per session
- agent sessions supports offset-cursor pagination over session summaries

Notes:
- Uses FakeStorageClient with in-memory JSONL fixtures to avoid external services.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-activity-001"


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


def test_agent_activity_returns_newest_first_with_cursor() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-03/1400Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-03T14:28:04Z",
                "event_type": "skill.completed",
                "skill_name": "Research",
                "status": "RUNNING",
                "task_id": "TK-1",
            },
            {
                "timestamp": "2026-05-03T14:32:07Z",
                "event_type": "task.scored",
                "tasks_scored": 14,
                "top_task_title": "Competitive analysis",
                "task_id": "TK-2",
            },
        ],
    )

    client = TestClient(_make_app(storage))

    response = client.get(
        "/app/v1/agent/activity",
        params={
            "from": "2026-05-03T14:00:00Z",
            "to": "2026-05-03T15:00:00Z",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    first_page = response.json()
    assert len(first_page["items"]) == 1
    assert first_page["items"][0]["event_type"] == "task.scored"
    assert first_page["next_cursor"] is not None

    second = client.get(
        "/app/v1/agent/activity",
        params={
            "from": "2026-05-03T14:00:00Z",
            "to": "2026-05-03T15:00:00Z",
            "limit": 1,
            "cursor": first_page["next_cursor"],
        },
    )

    assert second.status_code == 200
    second_page = second.json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["event_type"] == "skill.completed"


def test_agent_activity_filters_errors() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-03/1200Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-03T12:14:30Z",
                "event_type": "skill.completed",
                "status": "FAILED",
                "skill_name": "Research",
                "task_id": "TK-3",
            },
            {
                "timestamp": "2026-05-03T12:20:00Z",
                "event_type": "task.scored",
                "tasks_scored": 4,
            },
        ],
    )

    client = TestClient(_make_app(storage))

    response = client.get(
        "/app/v1/agent/activity",
        params={
            "from": "2026-05-03T12:00:00Z",
            "to": "2026-05-03T13:00:00Z",
            "type": "errors",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["status"] == "failed"


def test_agent_activity_rejects_large_range() -> None:
    client = TestClient(_make_app(FakeStorageClient()))

    response = client.get(
        "/app/v1/agent/activity",
        params={
            "from": "2026-05-01T00:00:00Z",
            "to": "2026-05-10T00:00:00Z",
        },
    )

    assert response.status_code == 400


def test_agent_activity_requires_auth() -> None:
    app = FastAPI()
    app.include_router(app_router)
    app.state.storage_client = FakeStorageClient()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/app/v1/agent/activity",
        params={
            "from": datetime(2026, 5, 3, 12, 0, tzinfo=UTC).isoformat(),
            "to": datetime(2026, 5, 3, 13, 0, tzinfo=UTC).isoformat(),
        },
    )
    assert response.status_code in (401, 403)


def test_agent_sessions_aggregates_by_session() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-03/1500Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-03T15:05:00Z",
                "event_type": "agent.tool_call",
                "session_id": "SES-a1",
                "input_tokens": 100,
                "output_tokens": 30,
            },
            {
                "timestamp": "2026-05-03T15:06:00Z",
                "event_type": "skill.completed",
                "session_id": "SES-a1",
                "status": "COMPLETED",
                "input_tokens": 40,
                "output_tokens": 10,
            },
            {
                "timestamp": "2026-05-03T15:07:00Z",
                "event_type": "outbound.sent",
                "session_id": "SES-a1",
            },
        ],
    )

    client = TestClient(_make_app(storage))
    response = client.get(
        "/app/v1/agent/sessions",
        params={
            "from": "2026-05-03T15:00:00Z",
            "to": "2026-05-03T16:00:00Z",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["nextCursor"] is None
    assert len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["sessionId"] == "SES-a1"
    assert row["toolCallCount"] == 1
    assert row["skillCount"] == 1
    assert row["messagesSent"] == 1
    assert row["messagesReceived"] == 0
    assert row["inputTokens"] == 140
    assert row["outputTokens"] == 40
    assert row["status"] == "completed"


def test_agent_sessions_supports_offset_cursor_pagination() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-03/1600Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-03T16:30:00Z",
                "event_type": "agent.tool_call",
                "session_id": "SES-new",
            },
            {
                "timestamp": "2026-05-03T16:10:00Z",
                "event_type": "skill.completed",
                "session_id": "SES-old",
            },
        ],
    )

    client = TestClient(_make_app(storage))

    first = client.get(
        "/app/v1/agent/sessions",
        params={
            "from": "2026-05-03T16:00:00Z",
            "to": "2026-05-03T17:00:00Z",
            "limit": 1,
            "cursor": 0,
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["sessionId"] == "SES-new"
    assert first_payload["nextCursor"] == 1

    second = client.get(
        "/app/v1/agent/sessions",
        params={
            "from": "2026-05-03T16:00:00Z",
            "to": "2026-05-03T17:00:00Z",
            "limit": 1,
            "cursor": first_payload["nextCursor"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["sessionId"] == "SES-old"
    assert second_payload["nextCursor"] is None
