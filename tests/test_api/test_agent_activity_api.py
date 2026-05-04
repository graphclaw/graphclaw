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
