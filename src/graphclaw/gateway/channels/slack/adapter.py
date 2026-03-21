# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""graphclaw.gateway.channels.slack.adapter — Slack channel adapter.

Description
-----------
Implements ``ChannelAdapter`` for the Slack Events API channel.  Slack uses
an incoming webhook model: Slack POSTs events to a URL exposed by this
gateway.  The adapter verifies the request signature (HMAC-SHA256) and
converts incoming payloads to ``InboundMessage`` objects.

Design Patterns
---------------
- Adapter: Wraps Slack Web API behind the ChannelAdapter interface.
- Graceful skip: If SLACK_BOT_TOKEN is missing, start() logs a warning
  and returns immediately.

Public API
----------
- SlackAdapter: ChannelAdapter implementation for Slack.

Dependencies
------------
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channels.slack.config: SlackConfig.
- graphclaw.gateway.channels.slack.normalizer: normalize_slack.
- graphclaw.gateway.channels.slack.sender: SlackSender.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
- graphclaw.infra.storage: StorageClient.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channels.slack.config import SlackConfig
from graphclaw.gateway.channels.slack.normalizer import normalize_slack
from graphclaw.gateway.channels.slack.sender import SlackSender
from graphclaw.gateway.schemas import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from graphclaw.infra.broker import MessageBroker
    from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelAdapter):
    """Slack Events API channel adapter (webhook-based)."""

    def __init__(self) -> None:
        self._config: SlackConfig | None = None
        self._sender: SlackSender | None = None
        self._broker: MessageBroker | None = None

    @property
    def channel_name(self) -> str:
        return "slack"

    async def start(self, broker: MessageBroker) -> None:
        """Load config from environment; log ready status (Slack uses webhooks)."""
        self._config = SlackConfig.from_env()
        if self._config is None:
            logger.warning(
                "Slack channel: SLACK_BOT_TOKEN not set — channel disabled"
            )
            return
        self._sender = SlackSender(self._config)
        self._broker = broker
        logger.info("Slack channel: ready (webhook mode)")

    async def stop(self) -> None:
        self._config = None
        self._sender = None
        self._broker = None
        logger.info("Slack channel: stopped")

    async def send(self, message: OutboundMessage) -> None:
        """Send an outbound text message via the Slack Web API."""
        if self._sender is None:
            logger.warning(
                "Slack channel: not configured, dropping outbound message %s",
                message.message_id,
            )
            return
        channel = message.recipient or (
            self._config.default_channel if self._config else "#general"
        )
        await self._sender.send(channel=channel, text=message.body)

    async def send_message(
        self,
        recipient: str,
        text: str,
        attachments: list | None = None,
    ) -> None:
        """Send a message directly to a Slack channel or user.

        Args:
            recipient: Slack channel ID/name or user ID to DM.
            text: Message body text.
            attachments: Unused (reserved for future block-kit support).
        """
        if self._sender is None:
            logger.warning("Slack channel: not configured, cannot send message")
            return
        await self._sender.send(channel=recipient, text=text)

    def verify_webhook_signature(
        self, body: bytes, timestamp: str, signature: str
    ) -> bool:
        """Verify a Slack webhook request signature using HMAC-SHA256.

        Slack signs each request with a signature derived from the signing secret,
        the request timestamp, and the raw request body.  The expected signature
        format is ``v0=<hex-digest>``.

        Args:
            body: Raw request body bytes.
            timestamp: Value of the ``X-Slack-Request-Timestamp`` header.
            signature: Value of the ``X-Slack-Signature`` header.

        Returns:
            ``True`` if the signature is valid, ``False`` otherwise.
        """
        if self._config is None or not self._config.signing_secret:
            logger.warning("Slack channel: signing_secret not configured")
            return False

        import hashlib  # noqa: PLC0415
        import hmac  # noqa: PLC0415

        sig_basestring = f"v0:{timestamp}:".encode() + body
        computed = (
            "v0="
            + hmac.new(
                self._config.signing_secret.encode(),
                sig_basestring,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(computed, signature)

    async def handle_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Parse a Slack event payload and publish any inbound message to the broker.

        Args:
            payload: Parsed JSON body of the Slack Events API callback.

        Returns:
            The ``InboundMessage`` if one was published, otherwise ``None``.
        """
        # Slack URL verification challenge
        if payload.get("type") == "url_verification":
            return None

        msg = normalize_slack(payload)
        if msg is None:
            logger.debug("Slack channel: event produced no inbound message")
            return None

        if self._broker is not None:
            await self._broker.publish("inbound_messages", msg.model_dump(mode="json"))
            logger.info("Slack channel: published inbound message %s", msg.message_id)
        else:
            logger.warning("Slack channel: broker not available, dropping message %s", msg.message_id)

        return msg
