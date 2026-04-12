"""tests.test_api.test_skill_registry_extended_routes — Wave 5 skill registry tests.

Covers:
- POST /skills/{id}/feedback
- GET  /skills/workers
- GET  /skills/{id}/executions
- POST /skills/{id}/test
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_skill_registry_service, get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.models.base import utcnow

from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-skills-ext-001"


# ---------------------------------------------------------------------------
# Fake SkillRegistryService
# ---------------------------------------------------------------------------

class FakeInstalledSkill:
    def __init__(self, skill_id: str, name: str = "test-skill") -> None:
        self.skill_id = skill_id
        self.name = name
        self.version = "0.1.0"
        self.description = "A test skill"
        self.source_uri = "local://test"
        self.tags = []
        self.usage_count = 0
        self.avg_quality_score = 0.0

        class SkillSourceTypeStub:
            value = "local"
        self.source_type = SkillSourceTypeStub()

    def record_usage(self, quality_score: float | None = None) -> None:
        self.usage_count += 1
        if quality_score is not None:
            self.avg_quality_score = (
                0.2 * quality_score + 0.8 * self.avg_quality_score
            )


class FakeSkillRegistry:
    """Minimal fake SkillRegistryService."""

    def __init__(self) -> None:
        self._installed: dict[str, FakeInstalledSkill] = {}

    def add_skill(self, skill_id: str, name: str = "test-skill") -> FakeInstalledSkill:
        sk = FakeInstalledSkill(skill_id=skill_id, name=name)
        self._installed[skill_id] = sk
        return sk

    async def list_installed(self, user_id: str) -> list[FakeInstalledSkill]:
        return list(self._installed.values())

    async def record_usage(
        self,
        user_id: str,
        skill_id: str,
        quality_score: float | None = None,
    ) -> None:
        if skill_id not in self._installed:
            raise KeyError(f"Installed skill not found: {skill_id!r}")
        sk = self._installed[skill_id]
        sk.record_usage(quality_score)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    storage: FakeStorageClient | None = None,
    registry: FakeSkillRegistry | None = None,
) -> tuple[FastAPI, FakeStorageClient, FakeSkillRegistry]:
    if storage is None:
        storage = FakeStorageClient()
    if registry is None:
        registry = FakeSkillRegistry()

    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_storage() -> FakeStorageClient:
        return storage

    async def _fake_registry() -> FakeSkillRegistry:
        return registry

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage
    app.dependency_overrides[get_skill_registry_service] = _fake_registry
    return app, storage, registry


# ---------------------------------------------------------------------------
# POST /app/v1/skills/{id}/feedback
# ---------------------------------------------------------------------------


def test_feedback_records_usage() -> None:
    """POST /skills/{id}/feedback records a quality rating."""
    app, _, registry = _make_app()
    registry.add_skill("test-skill-001")
    client = TestClient(app)
    response = client.post(
        "/app/v1/skills/test-skill-001/feedback",
        json={"rating": 0.9},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skill_id"] == "test-skill-001"
    assert data["recorded"] is True


def test_feedback_updates_quality_score() -> None:
    """POST /skills/{id}/feedback updates the EMA quality score."""
    app, _, registry = _make_app()
    sk = registry.add_skill("quality-sk")
    client = TestClient(app)
    client.post("/app/v1/skills/quality-sk/feedback", json={"rating": 1.0})
    assert sk.avg_quality_score > 0


def test_feedback_not_installed_returns_404() -> None:
    """POST /skills/{id}/feedback returns 404 for non-installed skill."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/skills/nonexistent-skill/feedback",
        json={"rating": 0.5},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /app/v1/skills/workers
# ---------------------------------------------------------------------------


def test_list_workers_no_pool_returns_empty() -> None:
    """GET /skills/workers returns [] when no worker pool is present."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/skills/workers")
    assert response.status_code == 200
    assert response.json() == []


def test_list_workers_with_pool_returns_statuses() -> None:
    """GET /skills/workers returns worker statuses when pool is present."""

    class WorkerStatusStub:
        worker_id = "worker-1"
        jobs_completed = 5
        jobs_failed = 0
        current_job_id = None
        last_heartbeat = None

        class state:
            value = "IDLE"

    class FakeWorkerPool:
        def get_worker_statuses(self) -> list:
            return [WorkerStatusStub()]

    app, _, _ = _make_app()
    app.state.worker_pool = FakeWorkerPool()
    client = TestClient(app)
    response = client.get("/app/v1/skills/workers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["worker_id"] == "worker-1"
    assert data[0]["jobs_completed"] == 5


# ---------------------------------------------------------------------------
# GET /app/v1/skills/{id}/executions
# ---------------------------------------------------------------------------


def test_list_executions_no_history_returns_empty() -> None:
    """GET /skills/{id}/executions returns [] when no history is stored."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/skills/some-skill/executions")
    assert response.status_code == 200
    assert response.json() == []


def test_list_executions_returns_stored_records() -> None:
    """GET /skills/{id}/executions returns stored execution records."""
    app, storage, _ = _make_app()

    records = [
        {
            "job_id": "JOB-001",
            "skill_name": "research-skill",
            "task_id": "TSK-TEST-0001-TST",
            "session_id": "sess-abc",
            "status": "COMPLETED",
            "output": "Research done",
            "error": None,
            "started_at": utcnow().isoformat(),
            "completed_at": utcnow().isoformat(),
            "tokens_used": 100,
            "cost_usd": 0.001,
        }
    ]
    path = f"{_TEST_USER}/skills/executions/research-skill.json"
    storage._data[path] = json.dumps(records).encode()

    client = TestClient(app)
    response = client.get("/app/v1/skills/research-skill/executions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["job_id"] == "JOB-001"


# ---------------------------------------------------------------------------
# POST /app/v1/skills/{id}/test
# ---------------------------------------------------------------------------


def test_test_skill_returns_202() -> None:
    """POST /skills/{id}/test returns 202 for an installed skill."""
    app, _, registry = _make_app()
    registry.add_skill("test-skill-abc")
    client = TestClient(app)
    response = client.post(
        "/app/v1/skills/test-skill-abc/test",
        json={"input_data": {"prompt": "Hello"}},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["skill_id"] == "test-skill-abc"
    assert data["status"] == "submitted"
    assert "job_id" in data


def test_test_skill_not_installed_returns_404() -> None:
    """POST /skills/{id}/test returns 404 for non-installed skill."""
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/skills/missing-skill/test",
        json={"input_data": {}},
    )
    assert response.status_code == 404
