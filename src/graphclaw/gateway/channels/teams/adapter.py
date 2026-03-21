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
"""graphclaw.gateway.channels.teams.adapter — Microsoft Teams channel adapter.

Description
-----------
Implements ``ChannelAdapter`` for the Microsoft Teams Bot Framework channel.
Teams delivers messages to the bot via HTTP POST (Activity payloads).  Outbound
messages are sent to a configured incoming webhook URL using Adaptive Cards.

Design Patterns
---------------
- Adapter: Wraps Teams Bot Framework behind the ChannelAdapter interface.
- Graceful skip: If TEAMS_TENANT_ID is missing, start() logs a warning
  and returns immediately.

Public API
----------
- TeamsAdapter: ChannelAdapter implementation for Microsoft Teams.

Dependencies
------------
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channels.teams.config: TeamsConfig.
- graphclaw.gateway.channels.teams.normalizer: normalize_teams.
- graphclaw.gateway.channels.teams.sender: TeamsSender.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channels.teams.config import TeamsConfig
from graphclaw.gateway.channels.teams.normalizer import normalize_teams
from graphclaw.gateway.channels.teams.sender import TeamsSender
from graphclaw.gateway.schemas import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class TeamsAdapter(ChannelAdapter):
    """Microsoft Teams Bot Framework channel adapter (webhook-based)."""

    def __init__(self) -> None:
        self._config: TeamsConfig | None = None
        self._sender: TeamsSender | None = None
        self._broker: MessageBroker | None = None

    @property
    def channel_name(self) -> str:
        return "teams"

    async def start(self, broker: MessageBroker) -> None:
        """Load config from environment; log ready status (Teams uses webhooks)."""
        self._config = TeamsConfig.from_env()
        if self._config is None:
            logger.warning(
                "Teams channel: TEAMS_TENANT_ID not set — channel disabled"
            )
            return
        self._sender = TeamsSender()
        self._broker = broker
        logger.info("Teams channel: ready (webhook mode)")

    async def stop(self) -> None:
        self._config = None
        self._sender = None
        self._broker = None
        logger.info("Teams channel: stopped")

    async def send(self, message: OutboundMessage) -> None:
        """Send an outbound text message via a Teams incoming webhook."""
        if self._sender is None or self._config is None:
            logger.warning(
                "Teams channel: not configured, dropping outbound message %s",
                message.message_id,
            )
            return
        webhook_url = message.recipient or self._config.webhook_url
        if not webhook_url:
            logger.warning(
                "Teams channel: no webhook URL available for message %s",
                message.message_id,
            )
            return
        await self._sender.send(webhook_url=webhook_url, text=message.body)

    async def send_message(self, webhook_url: str, text: str) -> None:
        """Send a message to a Teams channel via an incoming webhook URL.

        Args:
            webhook_url: Teams incoming webhook URL.
            text: Message body text.
        """
        if self._sender is None:
            logger.warning("Teams channel: not configured, cannot send message")
            return
        await self._sender.send(webhook_url=webhook_url, text=text)

    async def handle_activity(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Parse a Teams Activity payload and publish any inbound message to the broker.

        Args:
            payload: Parsed JSON body of a Teams Bot Framework Activity.

        Returns:
            The ``InboundMessage`` if one was published, otherwise ``None``.
        """
        msg = normalize_teams(payload)
        if msg is None:
            logger.debug("Teams channel: activity produced no inbound message")
            return None

        if self._broker is not None:
            await self._broker.publish("inbound_messages", msg.model_dump(mode="json"))
            logger.info("Teams channel: published inbound message %s", msg.message_id)
        else:
            logger.warning(
                "Teams channel: broker not available, dropping message %s",
                msg.message_id,
            )

        return msg
