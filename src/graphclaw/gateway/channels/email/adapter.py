# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channels.email.adapter — Email channel adapter.

Description
-----------
Implements ``ChannelAdapter`` for the email channel. Manages the EmailPoller
(IMAP inbound) and EmailSender (SMTP outbound) as background tasks.

Design Patterns
---------------
- Adapter: Wraps IMAP/SMTP protocol handling behind the ChannelAdapter interface.
- Facade: Unifies poller and sender lifecycle into a single adapter.

Public API
----------
- EmailChannelAdapter: ChannelAdapter implementation for email.

Dependencies
------------
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channels.email.poller: EmailPoller.
- graphclaw.gateway.channels.email.sender: EmailSender.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""

from __future__ import annotations

import asyncio
import logging
import os

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channels.email.poller import EmailPoller
from graphclaw.gateway.channels.email.sender import EmailSender
from graphclaw.gateway.schemas import OutboundMessage
from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class EmailChannelAdapter(ChannelAdapter):
    """Email channel adapter — manages IMAP polling and SMTP sending."""

    def __init__(self) -> None:
        self._poller: EmailPoller | None = None
        self._sender: EmailSender | None = None
        self._poller_task: asyncio.Task | None = None

    @property
    def channel_name(self) -> str:
        return "email"

    async def start(self, broker: MessageBroker) -> None:
        host = os.environ.get("GATEWAY_IMAP_HOST", "")
        user = os.environ.get("GATEWAY_IMAP_USER", "")
        password = os.environ.get("GATEWAY_IMAP_PASS", "")

        if not (host and user and password):
            logger.info("Email channel: IMAP not configured, skipping poller")
            return

        port = int(os.environ.get("GATEWAY_IMAP_PORT", "993"))
        folder = os.environ.get("GATEWAY_IMAP_FOLDER", "INBOX")
        poll_interval = int(os.environ.get("GATEWAY_IMAP_POLL_INTERVAL", "60"))

        self._poller = EmailPoller(
            host=host,
            port=port,
            username=user,
            password=password,
            folder=folder,
            poll_interval=poll_interval,
            broker=broker,
        )
        self._poller_task = asyncio.create_task(self._poller.start())
        logger.info("Email channel: IMAP poller started")

        # Set up SMTP sender if configured
        smtp_host = os.environ.get("GATEWAY_SMTP_HOST", "")
        smtp_port = int(os.environ.get("GATEWAY_SMTP_PORT", "587"))
        if smtp_host and user and password:
            self._sender = EmailSender(
                host=smtp_host,
                port=smtp_port,
                username=user,
                password=password,
            )
            logger.info("Email channel: SMTP sender configured")

    async def stop(self) -> None:
        if self._poller is not None:
            await self._poller.stop()
        if self._poller_task is not None:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        logger.info("Email channel: stopped")

    async def send(self, message: OutboundMessage) -> None:
        if self._sender is None:
            logger.warning(
                "Email channel: SMTP not configured, cannot send message %s", message.message_id
            )
            return
        await self._sender.send(
            recipient=message.recipient,
            subject=message.subject,
            body=message.body,
            in_reply_to=message.in_reply_to,
        )
