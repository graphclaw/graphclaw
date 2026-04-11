"""tests.test_api.test_settings_routes — Tests for GET/PATCH /app/v1/settings.

Description
-----------
Verifies that the settings endpoints return the correct responses for
authenticated requests and reject unauthenticated ones.

Design Patterns
---------------
- ``app.dependency_overrides``: Both ``require_auth`` and ``get_storage_client``
  are replaced with fakes, eliminating the need for a real JWT or S3 stack.
- ``FakeStorageClient``: In-memory bytes store — starts empty so GET returns
  default settings; PATCH writes and GET reads back the same object.
- ``TestClient``: Synchronous ASGI test client from ``fastapi.testclient``.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.deps: get_storage_client.
- graphclaw.auth.middleware: require_auth.
- fastapi: FastAPI (third-party).
- pytest: stdlib test runner.
- tests.test_api.conftest: FakeStorageClient.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

from tests.test_api.conftest import FakeStorageClient

# ---------------------------------------------------------------------------
# Test application + fixture
# ---------------------------------------------------------------------------

_TEST_USER = "test-user-settings-001"


def _make_app(user_id: str, storage: FakeStorageClient) -> FastAPI:
    """Build a minimal FastAPI app with app_router and auth/storage overridden."""
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return user_id

    async def _fake_storage() -> FakeStorageClient:
        return storage

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage
    return app


@pytest.fixture()
def settings_client():
    """TestClient wired to a test user with overridden auth and storage."""
    storage = FakeStorageClient()
    app = _make_app(_TEST_USER, storage)
    return TestClient(app)


@pytest.fixture()
def no_auth_client():
    """TestClient with NO auth override — require_auth will reject requests."""
    app = FastAPI()
    app.include_router(app_router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /app/v1/settings
# ---------------------------------------------------------------------------


def test_get_settings_returns_200(settings_client: TestClient) -> None:
    """GET /app/v1/settings returns HTTP 200 with expected fields."""
    response = settings_client.get("/app/v1/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == _TEST_USER
    assert "llm_provider" in data
    assert "timezone" in data
    assert "channels" in data


def test_get_settings_default_values(settings_client: TestClient) -> None:
    """GET /app/v1/settings returns sensible defaults for a new user."""
    response = settings_client.get("/app/v1/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["llm_provider"] == "litellm"
    assert data["timezone"] == "UTC"
    assert isinstance(data["channels"], list)


def test_get_settings_requires_auth(no_auth_client: TestClient) -> None:
    """GET /app/v1/settings returns 401/403 without a Bearer token."""
    response = no_auth_client.get("/app/v1/settings")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PATCH /app/v1/settings
# ---------------------------------------------------------------------------


def test_patch_settings_updates_timezone(settings_client: TestClient) -> None:
    """PATCH /app/v1/settings updates timezone and returns updated settings."""
    response = settings_client.patch("/app/v1/settings", json={"timezone": "America/New_York"})
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == "America/New_York"


def test_patch_settings_updates_llm_provider(settings_client: TestClient) -> None:
    """PATCH /app/v1/settings updates llm_provider."""
    response = settings_client.patch("/app/v1/settings", json={"llm_provider": "anthropic"})
    assert response.status_code == 200
    data = response.json()
    assert data["llm_provider"] == "anthropic"


def test_patch_settings_partial_update(settings_client: TestClient) -> None:
    """PATCH /app/v1/settings with a partial body only changes supplied fields."""
    # First set both fields
    settings_client.patch(
        "/app/v1/settings",
        json={"llm_provider": "openai", "timezone": "Europe/London"},
    )
    # Then patch only llm_provider
    response = settings_client.patch("/app/v1/settings", json={"llm_provider": "litellm"})
    assert response.status_code == 200
    data = response.json()
    assert data["llm_provider"] == "litellm"
    assert data["timezone"] == "Europe/London"  # unchanged


def test_patch_settings_requires_auth(no_auth_client: TestClient) -> None:
    """PATCH /app/v1/settings returns 401/403 without a Bearer token."""
    response = no_auth_client.patch("/app/v1/settings", json={"timezone": "UTC"})
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /app/v1/settings/channels
# ---------------------------------------------------------------------------


def test_get_channels_returns_list(settings_client: TestClient) -> None:
    """GET /app/v1/settings/channels returns a list (empty for new user)."""
    response = settings_client.get("/app/v1/settings/channels")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_channels_requires_auth(no_auth_client: TestClient) -> None:
    """GET /app/v1/settings/channels returns 401/403 without a Bearer token."""
    response = no_auth_client.get("/app/v1/settings/channels")
    assert response.status_code in (401, 403)
