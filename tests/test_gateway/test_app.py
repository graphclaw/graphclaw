"""Tests for graphclaw.gateway.app — FastAPI application factory.

Requires httpx with ASGI transport support:
    pip install httpx  # or add httpx to dev dependencies in pyproject.toml
WS-I is responsible for adding httpx to pyproject.toml dev dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

# httpx is required for ASGI transport testing; WS-I adds it to dev deps.
try:
    from httpx import ASGITransport, AsyncClient

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from graphclaw.gateway.app import create_app
from graphclaw.gateway.schemas import InboundMessage

pytestmark = pytest.mark.skipif(
    not _HTTPX_AVAILABLE,
    reason="httpx not installed; WS-I adds it to dev dependencies",
)

# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------


class MockBroker:
    """Minimal in-memory MessageBroker stub for testing."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self._closed = False

    async def publish(self, queue: str, message: str) -> None:
        self.published.append((queue, message))

    async def consume(self, queue: str) -> AsyncIterator[str]:
        # Yields nothing — tests don't need live consumption
        return
        yield  # make it an async generator

    async def acknowledge(self, queue: str, message_id: str) -> None:
        pass

    async def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_broker() -> MockBroker:
    return MockBroker()


@pytest.fixture
def app_with_broker(mock_broker: MockBroker):
    """FastAPI app wired with a mock broker and email poller disabled."""
    with patch.dict(
        "os.environ",
        {},
        clear=False,
    ):
        # Ensure IMAP env vars are absent so no poller starts
        import os

        os.environ.pop("GATEWAY_IMAP_HOST", None)
        os.environ.pop("GATEWAY_IMAP_USER", None)
        os.environ.pop("GATEWAY_IMAP_PASS", None)
        app = create_app(broker=mock_broker)
        # Pre-set state so tests work without full lifespan startup
        app.state.broker = mock_broker
        yield app


@pytest.fixture
def app_no_broker():
    """FastAPI app with no broker (degraded mode)."""
    import os

    os.environ.pop("GATEWAY_IMAP_HOST", None)
    os.environ.pop("GATEWAY_IMAP_USER", None)
    os.environ.pop("GATEWAY_IMAP_PASS", None)
    return create_app(broker=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _sample_inbound_payload() -> dict[str, Any]:
    return {
        "message_id": "test-msg-001",
        "channel": "email",
        "sender": "alice@example.com",
        "subject": "Hello",
        "body": "Test body",
        "received_at": _NOW.isoformat(),
        "session_id": "SES-test-001",
    }


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_endpoint(self, app_no_broker):
        async with AsyncClient(
            transport=ASGITransport(app=app_no_broker), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "gateway"

    async def test_health_endpoint_with_broker(self, app_with_broker):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_broker), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Readiness endpoint tests
# ---------------------------------------------------------------------------


class TestReadinessEndpoint:
    async def test_readiness_endpoint_with_broker(self, app_with_broker):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_broker), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "dependencies" in data
        assert data["dependencies"]["broker"]["ok"] is True

    async def test_readiness_endpoint_no_broker_returns_503(self, app_no_broker):
        async with AsyncClient(
            transport=ASGITransport(app=app_no_broker), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert "dependencies" in data
        assert data["dependencies"]["broker"]["ok"] is False


# ---------------------------------------------------------------------------
# Inbound endpoint tests
# ---------------------------------------------------------------------------


class TestInboundEndpoint:
    async def test_inbound_endpoint_returns_accepted(self, app_with_broker):
        payload = _sample_inbound_payload()
        async with AsyncClient(
            transport=ASGITransport(app=app_with_broker), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/inbound", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message_id"] == "test-msg-001"

    async def test_inbound_endpoint_publishes_to_broker(
        self, app_with_broker, mock_broker: MockBroker
    ):
        payload = _sample_inbound_payload()
        async with AsyncClient(
            transport=ASGITransport(app=app_with_broker), base_url="http://test"
        ) as client:
            await client.post("/api/v1/inbound", json=payload)

        assert len(mock_broker.published) == 1
        queue, message_json = mock_broker.published[0]
        from graphclaw.infra.broker import INBOUND_MESSAGES

        assert queue == INBOUND_MESSAGES
        restored = InboundMessage.model_validate_json(message_json)
        assert restored.message_id == "test-msg-001"
        assert restored.sender == "alice@example.com"

    async def test_inbound_endpoint_no_broker_still_returns_accepted(self, app_no_broker):
        payload = _sample_inbound_payload()
        async with AsyncClient(
            transport=ASGITransport(app=app_no_broker), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/inbound", json=payload)
        # Should still accept even without a broker (message is dropped with warning)
        assert response.status_code == 202

    async def test_inbound_endpoint_invalid_payload_returns_422(self, app_with_broker):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_broker), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/inbound", json={"bad": "data"})
        assert response.status_code == 422
