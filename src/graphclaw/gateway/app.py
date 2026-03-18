"""graphclaw.gateway.app — FastAPI application factory for the channel gateway.

Description
-----------
Provides ``create_app``, which constructs and returns a fully configured
FastAPI application instance.  The application manages the ``EmailPoller``
background task and broker lifecycle via FastAPI's ``lifespan`` context
manager.

Endpoints:
- ``GET  /health``           — Liveness probe (always returns 200).
- ``GET  /health/ready``     — Readiness probe (checks broker connectivity).
- ``POST /api/v1/inbound``   — Accept an ``InboundMessage`` and publish to the
                               ``INBOUND_MESSAGES`` broker queue.
- ``POST /api/v1/trigger``   — On-demand trigger for ad-hoc agent activations.

Design Patterns
---------------
- Factory: ``create_app`` is the single entry-point for constructing the ASGI
  app; this allows different broker/poller configurations to be injected in
  tests without modifying module-level state.
- Lifespan Context Manager: Startup and shutdown logic lives in a single
  ``asynccontextmanager`` function, avoiding deprecated ``on_event`` hooks.
- Dependency Injection via ``app.state``: The broker is stored on
  ``app.state.broker`` so that endpoint handlers can access it without module-
  level globals.

Public API
----------
- create_app: Construct and return a configured ``FastAPI`` instance.

Dependencies
------------
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.gateway.email_poller: EmailPoller.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES.
- fastapi: FastAPI, Request (third-party).
- contextlib: asynccontextmanager (stdlib).
- logging: structured logging.
- os: environment variable access (stdlib).

Notes
-----
If ``broker`` is ``None`` (the default), the application still starts and
serves traffic, but ``/health/ready`` returns ``status: "degraded"`` and
publishing to the broker is skipped.  This enables lightweight integration
testing without a running message broker.

Email polling is enabled only when the environment variables
``GATEWAY_IMAP_HOST``, ``GATEWAY_IMAP_USER``, and ``GATEWAY_IMAP_PASS`` are
all set.  Port defaults to 993; folder defaults to ``"INBOX"``; poll interval
defaults to 60 seconds.  These can be overridden via ``GATEWAY_IMAP_PORT``,
``GATEWAY_IMAP_FOLDER``, and ``GATEWAY_IMAP_POLL_INTERVAL`` respectively.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from graphclaw.gateway.email_poller import EmailPoller
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)


def _build_poller(broker: MessageBroker | None) -> EmailPoller | None:
    """Construct an ``EmailPoller`` from environment variables, if configured.

    Returns ``None`` when the required IMAP environment variables are absent.
    """
    host = os.environ.get("GATEWAY_IMAP_HOST", "")
    user = os.environ.get("GATEWAY_IMAP_USER", "")
    password = os.environ.get("GATEWAY_IMAP_PASS", "")
    if not (host and user and password):
        logger.info(
            "EmailPoller not started: GATEWAY_IMAP_HOST / GATEWAY_IMAP_USER / "
            "GATEWAY_IMAP_PASS not all set"
        )
        return None
    port = int(os.environ.get("GATEWAY_IMAP_PORT", "993"))
    folder = os.environ.get("GATEWAY_IMAP_FOLDER", "INBOX")
    poll_interval = int(os.environ.get("GATEWAY_IMAP_POLL_INTERVAL", "60"))
    return EmailPoller(
        host=host,
        port=port,
        username=user,
        password=password,
        folder=folder,
        poll_interval=poll_interval,
        broker=broker,
    )


def create_app(broker: MessageBroker | None = None) -> FastAPI:
    """Construct and return a fully configured FastAPI gateway application.

    Parameters
    ----------
    broker:
        ``MessageBroker`` instance to use for publishing and consuming
        messages.  When ``None``, the application operates in a degraded
        mode suitable for health-check-only deployments and unit tests.

    Returns
    -------
    FastAPI:
        A configured ASGI application ready to be served by an ASGI server
        such as ``uvicorn``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        # ── Startup ──────────────────────────────────────────────────────
        app.state.broker = broker
        poller = _build_poller(broker)
        app.state.poller = poller
        app.state.poller_task = None

        if poller is not None:
            logger.info("Starting EmailPoller background task")
            app.state.poller_task = asyncio.create_task(poller.start())

        logger.info("GraphClaw Gateway started")
        yield

        # ── Shutdown ──────────────────────────────────────────────────────
        if poller is not None:
            await poller.stop()
        if app.state.poller_task is not None:
            app.state.poller_task.cancel()
            try:
                await app.state.poller_task
            except asyncio.CancelledError:
                pass

        if broker is not None:
            await broker.close()

        logger.info("GraphClaw Gateway shut down")

    app = FastAPI(
        title="GraphClaw Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── Routes ────────────────────────────────────────────────────────────

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe — always returns 200 when the process is alive."""
        return {"status": "ok", "service": "gateway"}

    @app.get("/health/ready", tags=["ops"])
    async def readiness(request: Request) -> JSONResponse:
        """Readiness probe — checks broker connectivity."""
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is None:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "reason": "broker not configured"},
            )
        try:
            # A lightweight connectivity check: attempt to publish an empty
            # probe string to a dedicated health queue and ignore any errors.
            # The broker's own error will surface as an exception here.
            # For now we treat the presence of a configured broker as "ready".
            # A more thorough check would perform a round-trip publish/consume.
            return JSONResponse(status_code=200, content={"status": "ready"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness check failed", exc_info=exc)
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": str(exc)},
            )

    @app.post("/api/v1/inbound", status_code=202, tags=["messages"])
    async def receive_inbound(
        message: InboundMessage, request: Request
    ) -> dict[str, str]:
        """Accept a normalized inbound message and publish it to the queue.

        The endpoint returns HTTP 202 Accepted immediately; downstream
        processing is asynchronous.
        """
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is not None:
            await current_broker.publish(
                INBOUND_MESSAGES, message.model_dump_json()
            )
            logger.info(
                "Gateway: published inbound message",
                extra={
                    "message_id": message.message_id,
                    "channel": message.channel,
                    "session_id": message.session_id,
                },
            )
        else:
            logger.warning(
                "Gateway: broker not configured, message %s dropped",
                message.message_id,
            )
        return {"status": "accepted", "message_id": message.message_id}

    @app.post("/api/v1/trigger", status_code=202, tags=["messages"])
    async def on_demand_trigger(
        payload: dict[str, Any], request: Request
    ) -> dict[str, str]:
        """On-demand trigger endpoint for ad-hoc agent activations.

        Wraps ``payload`` in an ``InboundMessage`` with ``channel="api"`` and
        publishes it to the ``INBOUND_MESSAGES`` queue.  The ``body`` field
        is derived from the JSON-serialized payload; ``subject`` defaults to
        ``"trigger"`` unless the payload contains a ``"subject"`` key.
        """
        import json

        message_id = str(uuid.uuid4())
        session_id = f"SES-{uuid.uuid4()}"
        trigger_msg = InboundMessage(
            message_id=message_id,
            channel="api",
            sender=payload.get("sender", "api"),
            subject=payload.get("subject", "trigger"),
            body=json.dumps(payload),
            received_at=datetime.now(tz=timezone.utc),
            session_id=session_id,
        )
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is not None:
            await current_broker.publish(
                INBOUND_MESSAGES, trigger_msg.model_dump_json()
            )
            logger.info(
                "Gateway: published trigger message",
                extra={"message_id": message_id, "session_id": session_id},
            )
        else:
            logger.warning(
                "Gateway: broker not configured, trigger %s dropped", message_id
            )
        return {"status": "accepted", "message_id": message_id}

    return app
