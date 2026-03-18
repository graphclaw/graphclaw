"""graphclaw.gateway — FastAPI channel gateway package.

Description
-----------
Entry point for the GraphClaw channel gateway. Provides the FastAPI application
factory and message channel integration components including IMAP email polling,
SMTP email sending, message normalization, and Pydantic schemas for inbound and
outbound messages.

Design Patterns
---------------
- Factory: ``create_app`` produces a fully configured FastAPI application instance.
- Adapter: ``EmailPoller`` and ``EmailSender`` adapt protocol-specific I/O to the
  broker's queue-based messaging abstraction.
- Strategy: Channel handling (email, API, CLI) is interchangeable via the shared
  ``InboundMessage`` / ``OutboundMessage`` schemas.

Public API
----------
- create_app: FastAPI application factory.
- InboundMessage: Pydantic model for normalized inbound messages.
- OutboundMessage: Pydantic model for outbound messages.
- EmailPoller: Background IMAP polling loop.
- EmailSender: SMTP outbound sender and queue consumer.
- normalize_email: Convert ``email.message.EmailMessage`` to ``InboundMessage``.

Dependencies
------------
- graphclaw.gateway.app: FastAPI application factory.
- graphclaw.gateway.schemas: Message Pydantic models.
- graphclaw.gateway.email_poller: IMAP polling loop.
- graphclaw.gateway.email_sender: SMTP sender.
- graphclaw.gateway.normalizer: Email normalization helper.

Notes
-----
The gateway depends on ``graphclaw.infra.broker`` for the ``MessageBroker``
interface and queue name constants. That module is built by WS-I and may not
be present during initial development; imports are guarded accordingly.
"""
from __future__ import annotations

from graphclaw.gateway.app import create_app
from graphclaw.gateway.email_poller import EmailPoller
from graphclaw.gateway.email_sender import EmailSender
from graphclaw.gateway.normalizer import normalize_email
from graphclaw.gateway.schemas import InboundMessage, OutboundMessage

__all__ = [
    "create_app",
    "EmailPoller",
    "EmailSender",
    "normalize_email",
    "InboundMessage",
    "OutboundMessage",
]
