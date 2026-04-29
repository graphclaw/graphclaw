"""tests.test_auth.test_oauth_redirect_validation — OAuth redirect URI hardening tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.auth.routes import _build_redirect_uri, get_oauth_service
from graphclaw.auth.routes import router as auth_router


def test_build_redirect_uri_defaults_to_localhost_allowlist(monkeypatch) -> None:
    monkeypatch.delenv("OAUTH_REDIRECT_BASE_URL", raising=False)
    monkeypatch.delenv("OAUTH_REDIRECT_ALLOWLIST", raising=False)

    redirect_uri = _build_redirect_uri("google")

    assert redirect_uri == "http://localhost:8000/auth/callback?provider=google"


def test_build_redirect_uri_rejects_non_localhost_http_base(monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "http://graphclaw.ai")
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", "http://graphclaw.ai")

    with pytest.raises(ValueError, match="must use https"):
        _build_redirect_uri("google")


def test_build_redirect_uri_rejects_base_not_in_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://api.graphclaw.ai")
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", "https://auth.graphclaw.ai")

    with pytest.raises(ValueError, match="ALLOWLIST"):
        _build_redirect_uri("github")


def test_build_redirect_uri_accepts_https_base_in_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://api.graphclaw.ai/")
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", " https://api.graphclaw.ai ")

    redirect_uri = _build_redirect_uri("microsoft")

    assert redirect_uri == "https://api.graphclaw.ai/auth/callback?provider=microsoft"


def test_login_returns_503_for_invalid_redirect_config(monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://api.graphclaw.ai")
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", "https://auth.graphclaw.ai")

    app = FastAPI()
    app.include_router(auth_router)

    mock_oauth = MagicMock()
    mock_oauth.get_authorization_url = AsyncMock(
        return_value=("https://example-idp.test/oauth", "state-123")
    )
    app.dependency_overrides[get_oauth_service] = lambda: mock_oauth

    client = TestClient(app)
    response = client.get("/auth/login", params={"provider": "google"})

    assert response.status_code == 503
    assert "ALLOWLIST" in response.json()["detail"]


def test_callback_rejects_unsupported_provider() -> None:
    app = FastAPI()
    app.include_router(auth_router)

    client = TestClient(app)
    response = client.get(
        "/auth/callback", params={"provider": "invalid", "code": "x", "state": "y"}
    )

    assert response.status_code == 400
    assert "Unsupported provider" in response.json()["detail"]
