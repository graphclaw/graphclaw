"""tests.test_api.test_agent_routes — Tests for /app/v1/agent/* endpoints.

Description
-----------
Covers the six agent monitor endpoints:

- GET  /app/v1/agent/status
- GET  /app/v1/agent/action-queue
- GET  /app/v1/agent/briefing
- GET  /app/v1/agent/triggers/schedule
- GET  /app/v1/agent/triggers/{id}
- POST /app/v1/agent/triggers/{id}/fire

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_auth`` replaced with a fake returning
  the test user; ``get_graph_store`` and ``get_scoring_engine`` replaced with
  in-memory fakes.
- ``app.state`` injection: ``trigger_engine`` and ``agent_loop`` objects are
  placed on ``app.state`` to simulate the runtime environment.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.deps: get_graph_store, get_scoring_engine.
- graphclaw.auth.middleware: require_auth.
- tests.test_api.conftest: FakeGraphStore.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store, get_scoring_engine
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.models.base import utcnow
from graphclaw.triggers.models import TriggerConfig, TriggerEvent, TriggerType

from tests.test_api.conftest import FakeGraphStore

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_TEST_USER = "USER-test-agent-001"


class FakeScoringEngine:
    """Minimal ScoringEngine stub — score_all returns an empty list."""

    async def score_all(self, tasks, context):
        return []


class FakeAgentLoop:
    """Fake AgentLoop that returns an empty action queue."""

    async def run_cycle(self):
        return []


class FakeTriggerScheduler:
    """Minimal scheduler that exposes _triggers dict."""

    def __init__(self):
        self._triggers: dict[str, TriggerConfig] = {}

    def register(self, config: TriggerConfig) -> None:
        self._triggers[config.trigger_id] = config


class FakeTriggerEngine:
    """Minimal TriggerEngine with a real-enough scheduler and fire_on_demand."""

    def __init__(self):
        self._scheduler = FakeTriggerScheduler()
        self._fired: list[TriggerEvent] = []

    async def fire_on_demand(self, user_id: str, payload: dict | None = None) -> TriggerEvent:
        import uuid

        event = TriggerEvent(
            trigger_id=f"TRIG-{uuid.uuid4()}",
            trigger_type=TriggerType.ON_DEMAND,
            user_id=user_id,
            payload=payload or {},
            created_at=utcnow(),
        )
        self._fired.append(event)
        return event


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_app(
    *,
    with_agent_loop: bool = False,
    with_trigger_engine: bool = False,
    trigger_configs: list[TriggerConfig] | None = None,
) -> tuple[FastAPI, FakeGraphStore, FakeTriggerEngine | None]:
    """Build a minimal FastAPI app with dependency and state overrides."""
    fake_store = FakeGraphStore()
    fake_scoring = FakeScoringEngine()

    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_store() -> FakeGraphStore:
        return fake_store

    async def _fake_scoring_dep() -> FakeScoringEngine:
        return fake_scoring

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_graph_store] = _fake_store
    app.dependency_overrides[get_scoring_engine] = _fake_scoring_dep

    fake_engine: FakeTriggerEngine | None = None

    if with_agent_loop:
        app.state.agent_loop = FakeAgentLoop()

    if with_trigger_engine:
        fake_engine = FakeTriggerEngine()
        if trigger_configs:
            for cfg in trigger_configs:
                fake_engine._scheduler.register(cfg)
        app.state.trigger_engine = fake_engine

    return app, fake_store, fake_engine


# ---------------------------------------------------------------------------
# GET /app/v1/agent/status
# ---------------------------------------------------------------------------


def test_agent_status_no_loop_returns_stopped() -> None:
    """GET /agent/status returns running=False when agent_loop is absent."""
    app, _, _ = _make_app(with_agent_loop=False)
    client = TestClient(app)
    response = client.get("/app/v1/agent/status")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is False


def test_agent_status_with_loop_returns_running() -> None:
    """GET /agent/status returns running=True when agent_loop is present."""
    app, _, _ = _make_app(with_agent_loop=True)
    client = TestClient(app)
    response = client.get("/app/v1/agent/status")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True
    assert "queue_depth" in data
    assert "agent_version" in data


def test_agent_status_requires_auth() -> None:
    """GET /agent/status returns 401/403 without auth."""
    app = FastAPI()
    app.include_router(app_router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/app/v1/agent/status")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /app/v1/agent/action-queue
# ---------------------------------------------------------------------------


def test_action_queue_no_loop_returns_empty() -> None:
    """GET /agent/action-queue returns [] when agent_loop is absent."""
    app, _, _ = _make_app(with_agent_loop=False)
    client = TestClient(app)
    response = client.get("/app/v1/agent/action-queue")
    assert response.status_code == 200
    assert response.json() == []


def test_action_queue_with_loop_returns_list() -> None:
    """GET /agent/action-queue returns list when agent_loop is present."""
    app, _, _ = _make_app(with_agent_loop=True)
    client = TestClient(app)
    response = client.get("/app/v1/agent/action-queue")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# GET /app/v1/agent/briefing
# ---------------------------------------------------------------------------


def test_briefing_returns_five_sections() -> None:
    """GET /agent/briefing returns a briefing with 5 expected sections."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/agent/briefing")
    assert response.status_code == 200
    data = response.json()
    assert "generated_at" in data
    assert "critical" in data
    assert "inferences" in data
    assert "completed" in data
    assert "ahead_of_curve" in data
    assert "deferred" in data


def test_briefing_sections_have_items_list() -> None:
    """Each briefing section has a 'items' list and 'title'."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/agent/briefing")
    data = response.json()
    for section_key in ("critical", "inferences", "completed", "ahead_of_curve", "deferred"):
        sec = data[section_key]
        assert isinstance(sec.get("items"), list), f"section {section_key!r} missing 'items'"
        assert "title" in sec, f"section {section_key!r} missing 'title'"


# ---------------------------------------------------------------------------
# GET /app/v1/agent/triggers/schedule
# ---------------------------------------------------------------------------


def _make_trigger_config(trigger_id: str) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=trigger_id,
        trigger_type=TriggerType.TIME_BASED,
        user_id=_TEST_USER,
        enabled=True,
        cron_expression="0 8 * * *",
        next_fire_at=None,
    )


def test_trigger_schedule_no_engine_returns_empty() -> None:
    """GET /agent/triggers/schedule returns [] when trigger engine is absent."""
    app, _, _ = _make_app(with_trigger_engine=False)
    client = TestClient(app)
    response = client.get("/app/v1/agent/triggers/schedule")
    assert response.status_code == 200
    assert response.json() == []


def test_trigger_schedule_returns_registered_triggers() -> None:
    """GET /agent/triggers/schedule returns all registered trigger configs."""
    cfg = _make_trigger_config("TRIG-daily-briefing")
    app, _, _ = _make_app(with_trigger_engine=True, trigger_configs=[cfg])
    client = TestClient(app)
    response = client.get("/app/v1/agent/triggers/schedule")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["trigger_id"] == "TRIG-daily-briefing"
    assert data[0]["trigger_type"] == "TIME_BASED"
    assert data[0]["cron_expression"] == "0 8 * * *"


# ---------------------------------------------------------------------------
# GET /app/v1/agent/triggers/{id}
# ---------------------------------------------------------------------------


def test_get_trigger_not_found_returns_404() -> None:
    """GET /agent/triggers/{id} returns 404 for unknown trigger ID."""
    app, _, _ = _make_app(with_trigger_engine=True)
    client = TestClient(app)
    response = client.get("/app/v1/agent/triggers/TRIG-nonexistent")
    assert response.status_code == 404


def test_get_trigger_returns_config() -> None:
    """GET /agent/triggers/{id} returns the trigger config when found."""
    cfg = _make_trigger_config("TRIG-my-trigger")
    app, _, _ = _make_app(with_trigger_engine=True, trigger_configs=[cfg])
    client = TestClient(app)
    response = client.get("/app/v1/agent/triggers/TRIG-my-trigger")
    assert response.status_code == 200
    data = response.json()
    assert data["trigger_id"] == "TRIG-my-trigger"
    assert data["enabled"] is True


def test_get_trigger_no_engine_returns_404() -> None:
    """GET /agent/triggers/{id} returns 404 when trigger engine is absent."""
    app, _, _ = _make_app(with_trigger_engine=False)
    client = TestClient(app)
    response = client.get("/app/v1/agent/triggers/TRIG-abc")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /app/v1/agent/triggers/{id}/fire
# ---------------------------------------------------------------------------


def test_fire_trigger_no_engine_returns_503() -> None:
    """POST /agent/triggers/{id}/fire returns 503 when trigger engine is absent."""
    app, _, _ = _make_app(with_trigger_engine=False)
    client = TestClient(app)
    response = client.post("/app/v1/agent/triggers/TRIG-abc/fire")
    assert response.status_code == 503


def test_fire_trigger_returns_202_with_event() -> None:
    """POST /agent/triggers/{id}/fire returns 202 with TriggerFireResponse."""
    app, _, fake_engine = _make_app(with_trigger_engine=True)
    client = TestClient(app)
    response = client.post("/app/v1/agent/triggers/TRIG-test/fire")
    assert response.status_code == 202
    data = response.json()
    assert data["trigger_type"] == "ON_DEMAND"
    assert data["user_id"] == _TEST_USER
    assert "fired_at" in data
    assert "trigger_id" in data


def test_fire_trigger_dispatches_event() -> None:
    """POST /agent/triggers/{id}/fire actually fires via the engine."""
    app, _, fake_engine = _make_app(with_trigger_engine=True)
    client = TestClient(app)
    client.post("/app/v1/agent/triggers/TRIG-test/fire")
    assert fake_engine is not None
    assert len(fake_engine._fired) == 1
    assert fake_engine._fired[0].user_id == _TEST_USER
