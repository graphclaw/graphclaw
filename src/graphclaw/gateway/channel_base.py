# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channel_base — Abstract base class for channel adapters.

Description
-----------
Defines the ``ChannelAdapter`` ABC that every gateway channel (email, WhatsApp,
Slack, Teams, etc.) must implement. The adapter encapsulates all channel-specific
I/O: polling for inbound messages, sending outbound messages, and lifecycle
management (start/stop).

Design Patterns
---------------
- Abstract Base Class: ``ChannelAdapter`` defines the minimal contract.
- Strategy: Different channel implementations are interchangeable at runtime.

Public API
----------
- ChannelAdapter: ABC with channel_name property, start, stop, send, can_handle.

Dependencies
------------
- abc: ABC, abstractmethod.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from graphclaw.gateway.schemas import OutboundMessage
from graphclaw.infra.broker import MessageBroker


class ChannelAdapter(ABC):
    """Abstract interface for a gateway channel."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Unique identifier for this channel (e.g. 'email', 'whatsapp')."""

    @abstractmethod
    async def start(self, broker: MessageBroker) -> None:
        """Start background tasks (pollers, webhook listeners, consumers)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop all background tasks gracefully."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Deliver an outbound message through this channel."""

    def can_handle(self, message: OutboundMessage) -> bool:
        """Return True if this adapter handles the message's channel."""
        return message.channel == self.channel_name
