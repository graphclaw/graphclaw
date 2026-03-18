"""graphclaw.gateway.routes.inbound — Inbound message acceptance endpoint.

Description
-----------
Provides ``POST /inbound/messages`` — the primary ingestion endpoint for the
GraphClaw channel gateway.  Accepts a normalised ``InboundMessage`` payload,
assigns a ``session_id`` if one is not already present, and publishes the
message to the ``INBOUND_MESSAGES`` broker queue for asynchronous processing
by the agent layer.

Design Patterns
---------------
- Command Endpoint: The route follows the command pattern — it accepts a
  message, validates it, dispatches to the broker, and immediately returns
  HTTP 202 Accepted without waiting for downstream processing.
- Dependency Injection: The broker is obtained via ``Depends(get_broker)``
  so that unit tests can override it with a mock without touching module-level
  state.

Public API
----------
- router: ``APIRouter`` instance.  Include in the application via
  ``app.include_router(router, prefix="/api/v1", tags=["inbound"])``.

Dependencies
------------
- graphclaw.gateway.deps: get_broker.
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES.
- fastapi: APIRouter, Depends, HTTPException (third-party).

Notes
-----
If the broker dependency raises ``RuntimeError`` (broker not initialised),
the exception propagates as HTTP 500.  Callers may detect this and fall back
to a degraded-mode response if needed.

A missing ``session_id`` is assigned here (not in the model) so that the
model remains a pure value object without side effects.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from graphclaw.gateway.deps import get_broker
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/inbound/messages", status_code=202, tags=["inbound"])
async def receive_message(
    message: InboundMessage,
    broker: MessageBroker = Depends(get_broker),
) -> dict[str, str]:
    """Accept an inbound message and publish it to the broker queue.

    Parameters
    ----------
    message:
        Normalised ``InboundMessage`` payload submitted by the caller.
    broker:
        ``MessageBroker`` injected via ``Depends``.

    Returns
    -------
    dict[str, str]:
        ``{"status": "accepted", "message_id": <id>}`` on success.

    Raises
    ------
    HTTPException (500):
        If broker publishing fails due to an unexpected error.
    """
    # Assign session_id if the caller did not provide one
    if not message.session_id:
        # Pydantic v2 models are immutable by default; use model_copy to patch
        message = message.model_copy(update={"session_id": f"SES-{uuid.uuid4()}"})

    try:
        await broker.publish(INBOUND_MESSAGES, message.model_dump_json())
        logger.info(
            "Gateway inbound: published message",
            extra={
                "message_id": message.message_id,
                "channel": message.channel,
                "session_id": message.session_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Gateway inbound: failed to publish message %s",
            message.message_id,
            exc_info=exc,
        )
        raise HTTPException(status_code=500, detail="Failed to publish message") from exc

    return {"status": "accepted", "message_id": message.message_id}
