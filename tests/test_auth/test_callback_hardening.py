# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_auth.test_callback_hardening — Tests for auth callback provisioning failure handling.

Description
-----------
Verifies that:
1. A provisioning failure returns HTTP 503 (not a token with oauth_subject as sub).
2. When no provisioning service is configured (dev/test mode), the token-only
   fallback still issues a token (existing dev-mode behaviour is preserved).
3. A successful provisioning failure never leaks an access_token in the body.

Design Patterns
---------------
- Uses FastAPI TestClient with dependency_overrides to control provisioning
  service behaviour without a live database.
- AsyncMock/MagicMock wiring mirrors test_oauth_redirect_validation.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.auth.jwt import JWTService
from graphclaw.auth.middleware import get_jwt_service
from graphclaw.auth.routes import get_oauth_service, get_provisioning_service
from graphclaw.auth.routes import router as auth_router

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_app(
    *,
    mock_oauth: MagicMock,
    provisioning_svc: object | None,
    jwt_svc: JWTService | None = None,
) -> FastAPI:
    """Return a minimal FastAPI app wired with the supplied dependency overrides."""
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_oauth_service] = lambda: mock_oauth
    app.dependency_overrides[get_provisioning_service] = lambda: provisioning_svc
    if jwt_svc is not None:
        app.dependency_overrides[get_jwt_service] = lambda: jwt_svc
    return app


def _mock_oauth(
    provider_user_id: str = "100671522771592774946",
    email: str = "user@example.com",
    name: str = "Test User",
) -> MagicMock:
    """Return an OAuthService mock that simulates a successful code exchange."""
    mock = MagicMock()
    mock.exchange_code = AsyncMock(
        return_value={
            "provider": "google",
            "provider_user_id": provider_user_id,
            "email": email,
            "name": name,
        }
    )
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCallbackProvisioningFailureHardening:
    def test_callback_returns_503_when_provisioning_raises(self) -> None:
        """A provisioning RuntimeError must result in HTTP 503, not a token."""
        failing_svc = MagicMock()
        failing_svc.provision_new_user = AsyncMock(side_effect=RuntimeError("db unavailable"))

        app = _make_app(mock_oauth=_mock_oauth(), provisioning_svc=failing_svc)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/auth/callback",
            params={"provider": "google", "code": "auth-code-xyz", "state": "csrf-state"},
            follow_redirects=False,
        )

        assert response.status_code == 503
        assert "provisioning" in response.json()["detail"].lower()

    def test_callback_503_response_does_not_contain_access_token(self) -> None:
        """On provisioning failure the response body must not carry an access_token."""
        failing_svc = MagicMock()
        failing_svc.provision_new_user = AsyncMock(side_effect=RuntimeError("db error"))

        app = _make_app(mock_oauth=_mock_oauth(), provisioning_svc=failing_svc)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/auth/callback",
            params={"provider": "google", "code": "auth-code-xyz", "state": "csrf-state"},
            follow_redirects=False,
        )

        body = response.json()
        assert "access_token" not in body

    def test_callback_returns_token_when_provisioning_service_is_none(self) -> None:
        """Dev/test mode: no provisioning service → token-only fallback still works."""
        jwt_svc = JWTService.from_env()
        app = _make_app(mock_oauth=_mock_oauth(), provisioning_svc=None, jwt_svc=jwt_svc)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/auth/callback",
            params={"provider": "google", "code": "auth-code-xyz", "state": "csrf-state"},
            follow_redirects=False,
        )

        # Expect redirect to OTC endpoint (not a 503 or 4xx)
        assert response.status_code in (200, 302, 307)
