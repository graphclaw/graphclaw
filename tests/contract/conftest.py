"""
Shared fixtures for contract tests.

Provides a running FastAPI app instance for schemathesis (v4 API).
The app is started in-process so no external service is needed.
"""
import pytest

pytest.importorskip("schemathesis", reason="schemathesis not installed — pip install '.[dev]' to run")

import schemathesis
import schemathesis.openapi

from graphclaw.gateway.app import create_app


@pytest.fixture(scope="session")
def graphclaw_app():
    return create_app(broker=None)


@pytest.fixture(scope="session")
def schema(graphclaw_app):
    # Use FastAPI's built-in openapi() to get the spec dict directly,
    # bypassing the HTTP rate-limiter middleware entirely.
    return schemathesis.openapi.from_dict(graphclaw_app.openapi())
