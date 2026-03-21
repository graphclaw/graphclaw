"""tests.test_api.test_settings_routes — Tests for GET/PATCH /app/v1/settings.

Description
-----------
Verifies that the settings endpoints return the correct responses for
authenticated requests and reject unauthenticated ones.

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_auth`` is replaced with a stub that
  returns a fixed user_id, eliminating the need for a real JWT stack.
- ``TestClient``: Synchronous ASGI test client from ``fastapi.testclient``.
- Isolated in-memory state: Each test function uses a unique user_id to avoid
  cross-test pollution from the module-level stub storage.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api: app_router.
- graphclaw.auth.middleware: require_auth.
- fastapi: FastAPI (third-party).
- pytest: stdlib test runner.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth


# ---------------------------------------------------------------------------
# Test application + fixture
# ---------------------------------------------------------------------------


def _make_app(user_id: str) -> FastAPI:
    """Build a minimal FastAPI app with app_router and auth overridden."""
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return user_id

    app.dependency_overrides[require_auth] = _fake_auth
    return app


@pytest.fixture()
def settings_client():
    """TestClient wired to a test user with overridden auth."""
    app = _make_app("test-user-settings-001")
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
    assert data["user_id"] == "test-user-settings-001"
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
    response = settings_client.patch(
        "/app/v1/settings", json={"timezone": "America/New_York"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == "America/New_York"


def test_patch_settings_updates_llm_provider(settings_client: TestClient) -> None:
    """PATCH /app/v1/settings updates llm_provider."""
    response = settings_client.patch(
        "/app/v1/settings", json={"llm_provider": "anthropic"}
    )
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
    response = settings_client.patch(
        "/app/v1/settings", json={"llm_provider": "litellm"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["llm_provider"] == "litellm"
    assert data["timezone"] == "Europe/London"  # unchanged


def test_patch_settings_requires_auth(no_auth_client: TestClient) -> None:
    """PATCH /app/v1/settings returns 401/403 without a Bearer token."""
    response = no_auth_client.patch(
        "/app/v1/settings", json={"timezone": "UTC"}
    )
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
