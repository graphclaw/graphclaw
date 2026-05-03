"""tests.test_api.test_conversation_routes — FR-STORE-001 / FR-UI-001 API tests.

Verifies:
  AC1: GET /conversations returns list of counterparties from index.
  AC2: GET /conversations returns empty list when no index exists.
  AC3: GET /conversations/{counterparty_id} lists threads.
  AC4: GET /conversations/{cp}/{channel}/{thread} returns messages newest-first.
  AC5: GET on missing thread returns empty list (not 404).
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-conv-001"

_INDEX_DATA = {
    "RES-bob-001": {
        "last_activity_at": "2024-11-01T10:00:00Z",
        "channels": ["telegram"],
        "thread_count": 1,
    },
    "RES-alice-002": {
        "last_activity_at": "2024-10-15T08:00:00Z",
        "channels": ["email"],
        "thread_count": 2,
    },
}

_THREAD_LINES = [
    json.dumps(
        {
            "direction": "out",
            "role": "agent",
            "content": "Hello, can you confirm receipt?",
            "timestamp": "2024-11-01T09:00:00Z",
            "task_id": "TASK-123",
        }
    ),
    json.dumps(
        {
            "direction": "in",
            "role": "counterparty",
            "content": "Yes, confirmed.",
            "timestamp": "2024-11-01T10:00:00Z",
        }
    ),
]


def _make_app(storage: FakeStorageClient) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)
    app.dependency_overrides[require_auth] = lambda: _TEST_USER
    app.dependency_overrides[get_storage_client] = lambda: storage
    return app


class TestListCounterparties:
    def test_returns_counterparties_from_index(self) -> None:
        storage = FakeStorageClient()
        idx_path = f"{_TEST_USER}/conversations/index.json"
        storage._data[idx_path] = json.dumps(_INDEX_DATA).encode()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        # Sorted by last_activity_at desc → RES-bob-001 first
        assert data[0]["counterparty_id"] == "RES-bob-001"
        assert data[0]["channels"] == ["telegram"]
        assert data[1]["counterparty_id"] == "RES-alice-002"

    def test_empty_list_when_no_index(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations")
        assert r.status_code == 200
        assert r.json() == []


class TestListThreads:
    def test_lists_threads_for_counterparty(self) -> None:
        storage = FakeStorageClient()
        # Add a JSONL file under the counterparty prefix
        path = f"{_TEST_USER}/conversations/RES-bob-001/telegram/tg-thread-001.jsonl"
        storage._data[path] = b"\n".join(line.encode() for line in _THREAD_LINES)
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-bob-001")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["channel"] == "telegram"
        assert data[0]["thread_id"] == "tg-thread-001"

    def test_empty_list_when_no_threads(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-nobody")
        assert r.status_code == 200
        assert r.json() == []


class TestReadThread:
    def test_returns_messages_newest_first(self) -> None:
        storage = FakeStorageClient()
        path = f"{_TEST_USER}/conversations/RES-bob-001/telegram/tg-thread-001.jsonl"
        storage._data[path] = b"\n".join(line.encode() for line in _THREAD_LINES)
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-bob-001/telegram/tg-thread-001?reverse=true")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        # newest first → inbound reply is first
        assert data[0]["direction"] == "in"
        assert data[1]["direction"] == "out"

    def test_returns_messages_oldest_first(self) -> None:
        storage = FakeStorageClient()
        path = f"{_TEST_USER}/conversations/RES-bob-001/telegram/tg-thread-001.jsonl"
        storage._data[path] = b"\n".join(line.encode() for line in _THREAD_LINES)
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-bob-001/telegram/tg-thread-001?reverse=false")
        assert r.status_code == 200
        data = r.json()
        assert data[0]["direction"] == "out"

    def test_missing_thread_returns_empty_list(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-nobody/email/no-thread?reverse=true")
        assert r.status_code == 200
        assert r.json() == []

    def test_limit_caps_messages(self) -> None:
        storage = FakeStorageClient()
        path = f"{_TEST_USER}/conversations/RES-bob-001/telegram/tg-thread-001.jsonl"
        storage._data[path] = b"\n".join(line.encode() for line in _THREAD_LINES)
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/conversations/RES-bob-001/telegram/tg-thread-001?limit=1")
        assert r.status_code == 200
        assert len(r.json()) == 1
