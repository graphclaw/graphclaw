# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channels.telegram.sender — Telegram outbound message delivery.

Sends text messages via the Telegram Bot API ``sendMessage`` method
using ``httpx`` async HTTP.

Environment Variables (via TelegramConfig)
------------------------------------------
TELEGRAM_BOT_TOKEN   Bot token from @BotFather.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.gateway.channels.telegram.config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramSender:
    """Delivers outbound messages via the Telegram Bot API."""

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config

    async def send(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        """Send a text message to *chat_id*.

        Args:
            chat_id: Telegram chat ID (numeric string or @username).
            text: Message content (max 4096 chars per Telegram limit).
            parse_mode: Optional Telegram parse mode — ``"HTML"``, ``"Markdown"``,
                ``"MarkdownV2"``, or ``None`` for plain text. Defaults to ``None``
                (plain text) to avoid entity-parsing errors with raw user data such
                as email addresses.

        Raises:
            RuntimeError: If the API call fails or httpx is not installed.
        """
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for the Telegram channel. "
                "Install it with: pip install 'httpx>=0.27.0'"
            ) from exc

        url = f"{self._config.api_base_url}/sendMessage"
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)

        if not response.is_success:
            raise RuntimeError(
                f"Telegram API sendMessage failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )
        logger.info("Telegram message sent to chat_id=%s", chat_id)
