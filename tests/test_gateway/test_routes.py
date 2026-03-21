"""Tests for graphclaw.gateway.routes — health, inbound, and outbound endpoints.

Uses ``httpx.AsyncClient`` with ASGI transport for in-process HTTP testing.
The broker dependency is overridden via ``app.dependency_overrides`` so no
real Redis connection is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

# httpx is required for ASGI transport; skip gracefully if not installed.
try:
    from httpx import ASGITransport, AsyncClient

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from contextlib import asynccontextmanager

from fastapi import FastAPI

from graphclaw.gateway import deps
from graphclaw.gateway.routes import health, inbound, outbound
from graphclaw.gateway.routes.health import _get_broker_optional

pytestmark = pytest.mark.skipif(
    not _HTTPX_AVAILABLE,
    reason="httpx not installed — add it to dev dependencies",
)

# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------


class MockBroker:
    """Minimal in-memory MessageBroker stub for route-level tests."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self._closed = False

    async def publish(self, queue: str, message: str) -> None:
        self.published.append((queue, message))

    async def consume(self, queue: str) -> AsyncIterator[str]:
        return
        yield  # make it an async generator

    async def acknowledge(self, queue: str, message_id: str) -> None:
        pass

    async def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# Helpers: build a minimal test app using the route routers
# ---------------------------------------------------------------------------


def _make_test_app(mock_broker: MockBroker | None = None) -> FastAPI:
    """Build a minimal FastAPI app that includes the gateway routers."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        yield

    app = FastAPI(title="test-gateway", lifespan=lifespan)
    app.include_router(health.router, tags=["health"])
    app.include_router(inbound.router, prefix="/api/v1", tags=["inbound"])
    app.include_router(outbound.router, prefix="/api/v1", tags=["outbound"])

    if mock_broker is not None:
        # Override both the direct broker dependency (for inbound/outbound routes)
        # and the optional broker dependency (for the readiness health check).
        app.dependency_overrides[deps.get_broker] = lambda: mock_broker
        app.dependency_overrides[_get_broker_optional] = lambda: mock_broker

    return app


_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _sample_inbound() -> dict[str, Any]:
    return {
        "message_id": "test-msg-001",
        "channel": "email",
        "sender": "alice@example.com",
        "subject": "Hello",
        "body": "Test body",
        "received_at": _NOW.isoformat(),
        "session_id": "SES-test-001",
    }


def _sample_outbound() -> dict[str, Any]:
    return {
        "message_id": "out-msg-001",
        "channel": "email",
        "recipient": "bob@example.com",
        "subject": "Reply",
        "body": "Here is my reply.",
        "created_at": _NOW.isoformat(),
        "session_id": "SES-test-001",
    }


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_health_check_returns_ok(self):
        app = _make_test_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"

    async def test_health_check_has_services_field(self):
        app = _make_test_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        data = response.json()
        assert "services" in data


class TestReadinessCheck:
    async def test_readiness_returns_ok_with_broker(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"

    async def test_readiness_returns_503_without_broker(self):
        # No broker override — get_broker will raise RuntimeError
        app = _make_test_app(mock_broker=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"


# ---------------------------------------------------------------------------
# Inbound endpoint tests
# ---------------------------------------------------------------------------


class TestInboundMessageAccepted:
    async def test_inbound_message_accepted(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        payload = _sample_inbound()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/inbound/messages", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message_id"] == "test-msg-001"

    async def test_inbound_message_invalid_body_returns_422(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/inbound/messages", json={"invalid": "payload"})
        assert response.status_code == 422

    async def test_inbound_publishes_to_broker(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        payload = _sample_inbound()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/inbound/messages", json=payload)

        assert len(broker.published) == 1
        queue, message_json = broker.published[0]
        from graphclaw.gateway.schemas import InboundMessage
        from graphclaw.infra.broker import INBOUND_MESSAGES

        assert queue == INBOUND_MESSAGES
        restored = InboundMessage.model_validate_json(message_json)
        assert restored.message_id == "test-msg-001"
        assert restored.sender == "alice@example.com"

    async def test_inbound_assigns_session_id_if_missing(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        # Remove session_id from payload
        payload = {k: v for k, v in _sample_inbound().items() if k != "session_id"}
        payload["session_id"] = ""  # InboundMessage requires the field
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/inbound/messages", json=payload)

        queue, message_json = broker.published[0]
        from graphclaw.gateway.schemas import InboundMessage

        restored = InboundMessage.model_validate_json(message_json)
        assert restored.session_id.startswith("SES-")


# ---------------------------------------------------------------------------
# Outbound endpoint tests
# ---------------------------------------------------------------------------


class TestOutboundMessageQueued:
    async def test_outbound_message_queued(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        payload = _sample_outbound()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/outbound/messages", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["message_id"] == "out-msg-001"

    async def test_outbound_publishes_to_broker(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        payload = _sample_outbound()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/outbound/messages", json=payload)

        assert len(broker.published) == 1
        queue, message_json = broker.published[0]
        from graphclaw.gateway.schemas import OutboundMessage
        from graphclaw.infra.broker import OUTBOUND_MESSAGES

        assert queue == OUTBOUND_MESSAGES
        restored = OutboundMessage.model_validate_json(message_json)
        assert restored.message_id == "out-msg-001"
        assert restored.recipient == "bob@example.com"

    async def test_outbound_invalid_body_returns_422(self):
        broker = MockBroker()
        app = _make_test_app(mock_broker=broker)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/outbound/messages", json={"bad": "data"})
        assert response.status_code == 422
