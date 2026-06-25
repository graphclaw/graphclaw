# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway — FastAPI channel gateway package.

Description
-----------
Entry point for the GraphClaw channel gateway. Provides the FastAPI application
factory, the channel plugin architecture (ChannelAdapter, ChannelRegistry), and
message channel integration components including IMAP email polling, SMTP email
sending, message normalization, and Pydantic schemas for inbound and outbound
messages.

Design Patterns
---------------
- Factory: ``create_app`` produces a fully configured FastAPI application instance.
- Registry: ``ChannelRegistry`` manages channel adapters indexed by channel name.
- Plugin Discovery: ``build_registry`` loads channel adapters via importlib.
- Adapter: ``EmailPoller`` and ``EmailSender`` adapt protocol-specific I/O to the
  broker's queue-based messaging abstraction.
- Strategy: Channel handling (email, API, CLI) is interchangeable via the shared
  ``InboundMessage`` / ``OutboundMessage`` schemas.

Public API
----------
- create_app: FastAPI application factory.
- ChannelAdapter: Abstract base class for channel adapters.
- ChannelRegistry: Registry of active channel adapters.
- build_registry: Discover and load enabled channels into a registry.
- InboundMessage: Pydantic model for normalized inbound messages.
- OutboundMessage: Pydantic model for outbound messages.
- EmailPoller: Background IMAP polling loop.
- EmailSender: SMTP outbound sender and queue consumer.
- normalize_email: Convert ``email.message.EmailMessage`` to ``InboundMessage``.

Dependencies
------------
- graphclaw.gateway.app: FastAPI application factory.
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channel_registry: ChannelRegistry and build_registry.
- graphclaw.gateway.schemas: Message Pydantic models.
- graphclaw.gateway.channels.email.poller: IMAP polling loop.
- graphclaw.gateway.channels.email.sender: SMTP sender.
- graphclaw.gateway.channels.email.normalizer: Email normalization helper.

Notes
-----
The gateway depends on ``graphclaw.infra.broker`` for the ``MessageBroker``
interface and queue name constants.
"""

from __future__ import annotations

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channel_registry import ChannelRegistry, build_registry
from graphclaw.gateway.channels.email.normalizer import normalize_email
from graphclaw.gateway.channels.email.poller import EmailPoller
from graphclaw.gateway.channels.email.sender import EmailSender
from graphclaw.gateway.schemas import InboundMessage, OutboundMessage


def create_app(*args: object, **kwargs: object):
    """Lazy import to avoid package init circular imports during test collection."""
    from graphclaw.gateway.app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = [
    "create_app",
    "ChannelAdapter",
    "ChannelRegistry",
    "build_registry",
    "InboundMessage",
    "OutboundMessage",
    "EmailPoller",
    "EmailSender",
    "normalize_email",
]
