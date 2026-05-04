"""
Shared fixtures for contract tests.

Provides a running FastAPI app instance via pytest-asyncio + httpx.AsyncClient.
The app is started in-process so no external service is needed for contract tests.
"""
import pytest
import schemathesis
from httpx import AsyncClient

from graphclaw.main import app as fastapi_app


@pytest.fixture(scope="session")
def graphclaw_app():
    return fastapi_app


@pytest.fixture(scope="session")
def schema(graphclaw_app):
    return schemathesis.from_asgi(
        "/openapi.json",
        app=graphclaw_app,
        validate_schema=False,
    )
