"""
GC-U-API-W50-003 - validates comms summary aggregation route.

Scenario: The comms summary endpoint should aggregate inbound/outbound counters
for one UTC day and report matched versus unmatched inbound outcomes.

PRD: docs/cockpit-backend-api-prd.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-05

Cases covered:
- aggregates daily comms counters from inbound and outbound events
- defaults to current UTC day when date query param is omitted
- enforces authentication for comms summary route

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

_TEST_USER = "USER-test-comms-001"


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


def test_get_comms_summary_aggregates_daily_counts() -> None:
    storage = FakeStorageClient()
    key = f"{_TEST_USER}/logs/agent/2026-05-05/1400Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": "2026-05-05T14:05:00Z",
                "event_type": "inbound.processed",
                "action": "state_update_published",
            },
            {
                "timestamp": "2026-05-05T14:08:00Z",
                "event_type": "inbound.processed",
                "action": "manual_match_required",
            },
            {
                "timestamp": "2026-05-05T14:10:00Z",
                "event_type": "outbound.sent",
            },
            {
                "timestamp": "2026-05-05T14:11:00Z",
                "event_type": "agent.message",
            },
        ],
    )

    client = TestClient(_make_app(storage))
    response = client.get("/app/v1/comms/summary", params={"date": "2026-05-05"})

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "date": "2026-05-05",
        "received": 2,
        "sent": 2,
        "matched": 1,
        "unmatched": 1,
    }


def test_get_comms_summary_defaults_to_current_utc_day() -> None:
    storage = FakeStorageClient()
    today = datetime.now(tz=UTC).date().isoformat()
    key = f"{_TEST_USER}/logs/agent/{today}/0900Z.jsonl"
    _write_log_file(
        storage,
        key,
        [
            {
                "timestamp": f"{today}T09:15:00Z",
                "event_type": "inbound.processed",
                "action": "state_update_published",
            }
        ],
    )

    client = TestClient(_make_app(storage))
    response = client.get("/app/v1/comms/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == today
    assert payload["received"] == 1
    assert payload["matched"] == 1
    assert payload["unmatched"] == 0


def test_get_comms_summary_requires_auth() -> None:
    app = FastAPI()
    app.include_router(app_router)
    app.state.storage_client = FakeStorageClient()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/app/v1/comms/summary", params={"date": "2026-05-05"})

    assert response.status_code in (401, 403)
