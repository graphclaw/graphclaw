# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
GC-U-API-W50-001 - exposes active sub-agent delegations for the cockpit agents panel.

Scenario: The API should return active delegation rows from in-memory sub-agent
runner snapshots and dispatch tiers, gracefully returning empty payloads when
the pool is not available.

PRD: docs/cockpit-backend-api-prd.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-05

Cases covered:
- returns empty list when sub-agent pool is not initialised
- maps runner status snapshots to delegation rows
- sorts delegations by started_at descending
- returns empty dispatch plan when sub-agent pool is not initialised
- returns dispatch plan tiers for a session id
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.agent.sub_agent_runner import RunnerState, RunnerStatus
from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.models.base import utcnow
from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-delegations-001"


class _FakeSubAgentPool:
    def __init__(
        self,
        statuses: list[RunnerStatus],
        dispatch_plans: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        self._statuses = statuses
        self._dispatch_plans = dispatch_plans or {}

    def get_runner_statuses(self) -> list[RunnerStatus]:
        return self._statuses

    def get_dispatch_plan(self, session_id: str) -> list[dict[str, object]]:
        return self._dispatch_plans.get(session_id, [])


def _make_app(pool: _FakeSubAgentPool | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_storage() -> FakeStorageClient:
        return FakeStorageClient()

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage

    if pool is not None:
        app.state.sub_agent_pool = pool

    return app


def test_list_delegations_returns_empty_when_pool_missing() -> None:
    app = _make_app(pool=None)
    client = TestClient(app)

    response = client.get("/app/v1/agents/delegations")

    assert response.status_code == 200
    assert response.json() == []


def test_list_delegations_maps_runner_status() -> None:
    now = utcnow()
    pool = _FakeSubAgentPool(
        [
            RunnerStatus(
                runner_id="runner-001",
                state=RunnerState.RUNNING,
                agent_id="agent-alpha",
                task_id="TSK-123",
                session_id="ses-alpha",
                batch_id="batch-1",
                started_at=now - timedelta(seconds=180),
                last_heartbeat=now - timedelta(seconds=25),
                elapsed_ms=180000,
            )
        ]
    )

    app = _make_app(pool=pool)
    client = TestClient(app)

    response = client.get("/app/v1/agents/delegations")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent-alpha"
    assert rows[0]["task_id"] == "TSK-123"
    assert rows[0]["session_id"] == "ses-alpha"
    assert rows[0]["status"] == "RUNNING"
    assert rows[0]["duration_seconds"] >= 170
    assert rows[0]["heartbeat_age_seconds"] >= 20


def test_list_delegations_sorts_latest_first() -> None:
    now = utcnow()
    pool = _FakeSubAgentPool(
        [
            RunnerStatus(
                runner_id="runner-old",
                state=RunnerState.RUNNING,
                agent_id="agent-old",
                task_id="TSK-old",
                session_id="ses-old",
                batch_id="batch-old",
                started_at=now - timedelta(seconds=600),
                last_heartbeat=now - timedelta(seconds=60),
                elapsed_ms=600000,
            ),
            RunnerStatus(
                runner_id="runner-new",
                state=RunnerState.RUNNING,
                agent_id="agent-new",
                task_id="TSK-new",
                session_id="ses-new",
                batch_id="batch-new",
                started_at=now - timedelta(seconds=60),
                last_heartbeat=now - timedelta(seconds=10),
                elapsed_ms=60000,
            ),
        ]
    )

    app = _make_app(pool=pool)
    client = TestClient(app)

    response = client.get("/app/v1/agents/delegations")

    assert response.status_code == 200
    rows = response.json()
    assert [row["task_id"] for row in rows] == ["TSK-new", "TSK-old"]


def test_get_dispatch_plan_returns_empty_when_pool_missing() -> None:
    app = _make_app(pool=None)
    client = TestClient(app)

    response = client.get("/app/v1/agents/dispatch-plan/ses-missing")

    assert response.status_code == 200
    assert response.json() == {"session_id": "ses-missing", "tiers": []}


def test_get_dispatch_plan_returns_tiers_for_session() -> None:
    pool = _FakeSubAgentPool(
        statuses=[],
        dispatch_plans={
            "ses-123": [
                {
                    "tier": 1,
                    "batch_id": "batch-ses123-t1",
                    "total_count": 2,
                    "completed_count": 1,
                    "status": "RUNNING",
                    "jobs": [
                        {
                            "agent_id": "agent-a",
                            "task_id": "TSK-1",
                            "batch_id": "batch-ses123-t1",
                            "status": "RUNNING",
                        },
                        {
                            "agent_id": "agent-b",
                            "task_id": "TSK-2",
                            "batch_id": "batch-ses123-t1",
                            "status": "PENDING",
                        },
                    ],
                }
            ]
        },
    )

    app = _make_app(pool=pool)
    client = TestClient(app)

    response = client.get("/app/v1/agents/dispatch-plan/ses-123")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ses-123"
    assert len(payload["tiers"]) == 1
    assert payload["tiers"][0]["status"] == "RUNNING"
    assert payload["tiers"][0]["jobs"][0]["task_id"] == "TSK-1"
