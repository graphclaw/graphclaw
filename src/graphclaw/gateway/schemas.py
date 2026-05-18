# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.schemas — Pydantic message models for the channel gateway.

Description
-----------
Defines the canonical data transfer objects (DTOs) used to represent messages
flowing through the GraphClaw channel gateway. ``InboundMessage`` represents any
normalized message arriving from an external channel (email, API, CLI) before it
is published to the broker queue. ``OutboundMessage`` represents a reply or
agent-initiated message queued for delivery to a recipient.

Design Patterns
---------------
- DTO / Value Object: Both models are immutable, schema-validated containers with
  no behaviour beyond serialization; business logic lives in the gateway layer.
- Pydantic v2: Uses ``model_dump_json`` / ``model_validate_json`` for fast
  JSON round-trips without extra dependencies.

Public API
----------
- InboundMessage: Normalized inbound message from any channel.
- OutboundMessage: Outbound message queued for delivery via any channel.

Dependencies
------------
- pydantic: BaseModel, Field.
- datetime: datetime (stdlib).

Notes
-----
``session_id`` follows the ``SES-{uuid4}`` convention established in the project
logging specification (PRD Section 32). It is populated by the normalizer or the
API endpoint that first creates the message, not by these models directly.
``message_id`` for email messages is taken from the ``Message-ID`` header;
for API/CLI messages it is a caller-supplied UUID string.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """Normalized inbound message from any channel.

    Attributes
    ----------
    message_id:
        Unique identifier for this message. For email, sourced from the
        ``Message-ID`` header; for API/CLI, a caller-supplied UUID string.
    channel:
        Origin channel. One of ``"email"``, ``"api"``, or ``"cli"``.
    sender:
        Email address or user/agent identifier of the message originator.
    subject:
        Email subject line or a short title for non-email messages.
    body:
        Plain-text body of the message.
    received_at:
        Timestamp at which the message was received by the gateway.
    raw_headers:
        Original transport-level headers (email headers for email messages).
    attachments:
        List of attachment filenames associated with this message.
    session_id:
        Distributed tracing identifier in the format ``SES-{uuid4}``.
        Empty string when not yet assigned.
    in_reply_to:
        ``Message-ID`` of the message this is a reply to, enabling threading.
        ``None`` for top-level messages.
    """

    message_id: str
    channel: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    raw_headers: dict[str, str] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    session_id: str = ""
    in_reply_to: str | None = None


class OutboundMessage(BaseModel):
    """Outbound message queued for delivery via any channel.

    Attributes
    ----------
    message_id:
        Unique identifier for this outbound message.
    channel:
        Target delivery channel. One of ``"email"``, ``"api"``, or ``"cli"``.
    recipient:
        Email address or user/agent identifier of the intended recipient.
    subject:
        Subject line for email delivery; title for other channels.
    body:
        Plain-text body of the message.
    created_at:
        Timestamp at which the message was created and queued.
    in_reply_to:
        ``Message-ID`` of the inbound message this is a reply to.
        ``None`` for agent-initiated outbound messages.
    session_id:
        Distributed tracing identifier. Should match the ``session_id`` of
        the originating ``InboundMessage`` when applicable.
    """

    message_id: str
    channel: str
    recipient: str
    subject: str
    body: str
    created_at: datetime
    in_reply_to: str | None = None
    session_id: str = ""
