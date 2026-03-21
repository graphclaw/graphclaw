"""graphclaw.gateway.channels.telegram.adapter — Telegram channel adapter.

Description
-----------
Implements ``ChannelAdapter`` for the Telegram Bot API channel. Supports
two modes:

- **Long-polling** (default, ``TELEGRAM_USE_WEBHOOK=false``): A background
  asyncio task calls ``getUpdates`` repeatedly with a 30-second timeout.
  No HTTPS or public URL required — ideal for local development.

- **Webhook mode** (``TELEGRAM_USE_WEBHOOK=true``): Inbound updates arrive
  via HTTP POST from Telegram. The gateway app must mount a route and call
  ``handle_update()``; the adapter itself does not register FastAPI routes.

Design Patterns
---------------
- Adapter: Wraps Telegram Bot API behind the ChannelAdapter interface.
- Graceful skip: If TELEGRAM_BOT_TOKEN is missing, start() logs a warning
  and returns immediately.

Public API
----------
- TelegramChannelAdapter: ChannelAdapter implementation for Telegram.

Dependencies
------------
- graphclaw.gateway.channel_base: ChannelAdapter ABC.
- graphclaw.gateway.channels.telegram.config: TelegramConfig.
- graphclaw.gateway.channels.telegram.normalizer: normalize_telegram.
- graphclaw.gateway.channels.telegram.sender: TelegramSender.
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from graphclaw.gateway.channel_base import ChannelAdapter
from graphclaw.gateway.channels.telegram.config import TelegramConfig
from graphclaw.gateway.channels.telegram.normalizer import normalize_telegram
from graphclaw.gateway.channels.telegram.sender import TelegramSender
from graphclaw.gateway.schemas import OutboundMessage

if TYPE_CHECKING:
    from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class TelegramChannelAdapter(ChannelAdapter):
    """Telegram Bot API channel adapter (long-poll or webhook mode)."""

    def __init__(self) -> None:
        self._config: TelegramConfig | None = None
        self._sender: TelegramSender | None = None
        self._broker: MessageBroker | None = None
        self._poll_task: asyncio.Task | None = None

    @property
    def channel_name(self) -> str:
        return "telegram"

    async def start(self, broker: MessageBroker) -> None:
        """Load config from environment; start long-poll loop if not webhook mode."""
        self._config = TelegramConfig.from_env()
        if self._config is None:
            logger.warning("Telegram channel: TELEGRAM_BOT_TOKEN not set — channel disabled")
            return
        self._sender = TelegramSender(self._config)
        self._broker = broker
        logger.info("Telegram channel: started (webhook=%s)", self._config.use_webhook)

        if not self._config.use_webhook:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        self._config = None
        self._sender = None
        self._broker = None
        logger.info("Telegram channel: stopped")

    async def send(self, message: OutboundMessage) -> None:
        """Send an outbound text message via Telegram Bot API."""
        if self._sender is None:
            logger.warning(
                "Telegram channel: not configured, dropping outbound message %s",
                message.message_id,
            )
            return
        # Use chat_id stored in recipient (set by normalizer via raw_headers)
        await self._sender.send(chat_id=message.recipient, text=message.body)

    # ------------------------------------------------------------------
    # Long-poll loop (used when TELEGRAM_USE_WEBHOOK=false)
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Continuously poll Telegram getUpdates and publish inbound messages."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError:
            logger.error(
                "Telegram channel: httpx is required for long-polling. "
                "Install it with: pip install 'httpx>=0.27.0'"
            )
            return

        offset: int | None = None
        config = self._config  # captured at start, may not be None here

        logger.info("Telegram long-poll loop started (timeout=%ds)", config.poll_timeout)
        async with httpx.AsyncClient(timeout=config.poll_timeout + 5.0) as client:
            while True:
                try:
                    params: dict[str, Any] = {"timeout": config.poll_timeout}
                    if offset is not None:
                        params["offset"] = offset

                    response = await client.get(
                        f"{config.api_base_url}/getUpdates",
                        params=params,
                    )
                    if response.is_success:
                        data = response.json()
                        updates = data.get("result", [])
                        for update in updates:
                            await self.handle_update(update)
                            # Advance offset past processed update
                            update_id = update.get("update_id")
                            if update_id is not None:
                                offset = update_id + 1
                    else:
                        logger.warning("Telegram getUpdates failed: HTTP %s", response.status_code)
                        await asyncio.sleep(5)
                except asyncio.CancelledError:
                    logger.info("Telegram long-poll loop cancelled")
                    raise
                except Exception as exc:
                    logger.error("Telegram long-poll error: %s", exc)
                    await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Shared update handler (used by both poll loop and webhook route)
    # ------------------------------------------------------------------

    async def handle_update(self, update: dict[str, Any]) -> int:
        """Parse a Telegram Update and publish inbound messages to the broker.

        Args:
            update: Parsed JSON body of a single Telegram Update object.

        Returns:
            Number of InboundMessage objects published.
        """
        if self._broker is None:
            logger.warning("Telegram channel: broker not available, dropping update")
            return 0

        messages = normalize_telegram(update)
        for msg in messages:
            await self._broker.publish("inbound_messages", msg.model_dump(mode="json"))

        if not messages:
            logger.debug(
                "Telegram channel: update %s had no text messages", update.get("update_id")
            )
        else:
            logger.info("Telegram channel: published %d inbound message(s)", len(messages))

        return len(messages)

    def verify_secret_token(self, token: str) -> bool:
        """Validate the X-Telegram-Bot-Api-Secret-Token header on webhook POSTs.

        Returns True if webhook_secret is unset (token verification disabled)
        or if the token matches exactly.
        """
        if self._config is None:
            return False
        if not self._config.webhook_secret:
            return True  # No secret configured → accept all
        import hmac  # noqa: PLC0415

        return hmac.compare_digest(token, self._config.webhook_secret)
