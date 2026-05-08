# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.routes.outbound — Outbound message queuing endpoint.

Description
-----------
Provides ``POST /outbound/messages`` — the endpoint for queuing an outbound
message for delivery through a channel (currently email, with API/CLI
channels planned for future phases).  The endpoint publishes the
``OutboundMessage`` payload to the ``OUTBOUND_MESSAGES`` broker queue and
returns HTTP 202 Accepted.  Actual delivery is handled asynchronously by the
``EmailSender`` consumer (or a future channel router).

Design Patterns
---------------
- Command Endpoint: Accepts, validates, dispatches, and immediately returns
  without waiting for delivery confirmation.
- Dependency Injection: The broker is obtained via ``Depends(get_broker)``
  to enable clean test overrides.

Public API
----------
- router: ``APIRouter`` instance.  Include in the application via
  ``app.include_router(router, prefix="/api/v1", tags=["outbound"])``.

Dependencies
------------
- graphclaw.gateway.deps: get_broker.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker, OUTBOUND_MESSAGES.
- fastapi: APIRouter, Depends, HTTPException (third-party).

Notes
-----
The outbound queue is consumed by ``EmailSender.start_consumer`` or future
channel-specific consumers.  This endpoint does not differentiate by channel;
routing to the correct sender is the responsibility of the consumer layer.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from graphclaw.gateway.deps import get_broker
from graphclaw.gateway.schemas import OutboundMessage
from graphclaw.infra.broker import OUTBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/outbound/messages", status_code=202, tags=["outbound"])
async def send_message(
    message: OutboundMessage,
    broker: MessageBroker = Depends(get_broker),
) -> dict[str, str]:
    """Queue an outbound message for delivery.

    Parameters
    ----------
    message:
        ``OutboundMessage`` payload to enqueue.
    broker:
        ``MessageBroker`` injected via ``Depends``.

    Returns
    -------
    dict[str, str]:
        ``{"status": "queued", "message_id": <id>}`` on success.

    Raises
    ------
    HTTPException (500):
        If broker publishing fails due to an unexpected error.
    """
    try:
        await broker.publish(OUTBOUND_MESSAGES, message.model_dump_json())
        logger.info(
            "Gateway outbound: queued message",
            extra={
                "message_id": message.message_id,
                "channel": message.channel,
                "recipient": message.recipient,
                "session_id": message.session_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Gateway outbound: failed to queue message %s",
            message.message_id,
            exc_info=exc,
        )
        raise HTTPException(status_code=500, detail="Failed to queue message") from exc

    return {"status": "queued", "message_id": message.message_id}
