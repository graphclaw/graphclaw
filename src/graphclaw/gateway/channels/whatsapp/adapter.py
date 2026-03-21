"""graphclaw.gateway.channels.whatsapp.adapter — WhatsApp channel adapter.

Description
-----------
Implements ``ChannelAdapter`` for the WhatsApp Business Cloud API channel.
Inbound messages arrive via webhook (POST from Meta); outbound messages are
sent via the Graph API using ``WhatsAppSender``.

The adapter does *not* register FastAPI routes directly — it stores the
config and sender so the gateway app can mount webhook routes and delegate
to ``handle_webhook()``.

Design Patterns
---------------
- Adapter: Wraps WhatsApp Cloud API behind the ChannelAdapter interface.
- Graceful skip: If env vars are missing, start() logs a warning and returns.

Public API
----------
- WhatsAppChannelAdapter: ChannelAdapter implementation for WhatsApp.

Dependencies
------------
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channels.whatsapp.config: WhatsAppConfig.
- graphclaw.gateway.channels.whatsapp.normalizer: normalize_whatsapp.
- graphclaw.gateway.channels.whatsapp.sender: WhatsAppSender.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Any

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channels.whatsapp.config import WhatsAppConfig
from graphclaw.gateway.channels.whatsapp.normalizer import normalize_whatsapp
from graphclaw.gateway.channels.whatsapp.sender import WhatsAppSender
from graphclaw.gateway.schemas import OutboundMessage

if TYPE_CHECKING:
    from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class WhatsAppChannelAdapter(ChannelAdapter):
    """WhatsApp Business Cloud API channel adapter."""

    def __init__(self) -> None:
        self._config: WhatsAppConfig | None = None
        self._sender: WhatsAppSender | None = None
        self._broker: MessageBroker | None = None

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    async def start(self, broker: MessageBroker) -> None:
        """Load config from environment; skip if incomplete."""
        self._config = WhatsAppConfig.from_env()
        if self._config is None:
            logger.warning(
                "WhatsApp channel: WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN / "
                "WHATSAPP_WEBHOOK_SECRET / WHATSAPP_VERIFY_TOKEN not set — channel disabled"
            )
            return
        self._sender = WhatsAppSender(self._config)
        self._broker = broker
        logger.info("WhatsApp channel: started (phone_id=%s)", self._config.phone_number_id)

    async def stop(self) -> None:
        self._config = None
        self._sender = None
        self._broker = None
        logger.info("WhatsApp channel: stopped")

    async def send(self, message: OutboundMessage) -> None:
        """Send an outbound text message via WhatsApp Cloud API."""
        if self._sender is None:
            logger.warning(
                "WhatsApp channel: not configured, dropping outbound message %s",
                message.message_id,
            )
            return
        await self._sender.send(recipient=message.recipient, body=message.body)

    # ------------------------------------------------------------------
    # Webhook helpers (called by gateway app routes)
    # ------------------------------------------------------------------

    def verify_webhook_token(self, token: str) -> bool:
        """Validate the hub.verify_token sent by Meta during webhook registration."""
        if self._config is None:
            return False
        return hmac.compare_digest(token, self._config.verify_token)

    def verify_signature(self, payload_bytes: bytes, signature_header: str) -> bool:
        """Validate the X-Hub-Signature-256 header on incoming webhook POSTs.

        Args:
            payload_bytes: Raw request body bytes.
            signature_header: Value of the ``X-Hub-Signature-256`` header
                (e.g. ``"sha256=abcdef..."``).

        Returns:
            True if the HMAC-SHA256 matches; False otherwise.
        """
        if self._config is None:
            return False
        if not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(
            self._config.webhook_secret.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        received = signature_header[len("sha256=") :]
        return hmac.compare_digest(expected, received)

    async def handle_webhook(self, payload: dict[str, Any]) -> int:
        """Parse a WhatsApp webhook payload and publish inbound messages to the broker.

        Args:
            payload: Parsed JSON body of the webhook POST.

        Returns:
            Number of InboundMessage objects published.
        """
        if self._broker is None:
            logger.warning("WhatsApp channel: broker not available, dropping webhook")
            return 0

        messages = normalize_whatsapp(payload)
        for msg in messages:
            await self._broker.publish("inbound_messages", msg.model_dump(mode="json"))

        if not messages:
            logger.debug("WhatsApp channel: webhook contained no text messages")
        else:
            logger.info("WhatsApp channel: published %d inbound message(s)", len(messages))

        return len(messages)
