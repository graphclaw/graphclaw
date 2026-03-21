"""graphclaw.gateway.channels.telegram.config — Telegram Bot API configuration.

Reads Telegram Bot credentials from environment variables.
All variables are optional at import time — missing values cause the
adapter to skip startup with a warning rather than crashing.

Environment Variables
---------------------
TELEGRAM_BOT_TOKEN        Bot token from @BotFather (e.g. "123456:ABC-DEF...").
TELEGRAM_WEBHOOK_SECRET   Optional secret token sent in X-Telegram-Bot-Api-Secret-Token
                          header for webhook signature verification.
TELEGRAM_USE_WEBHOOK      Set to "true" to use webhook mode; default is long-polling.
TELEGRAM_POLL_TIMEOUT     Long-poll timeout in seconds (default: 30).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramConfig:
    """Validated Telegram Bot API configuration."""

    bot_token: str
    webhook_secret: str = ""
    use_webhook: bool = False
    poll_timeout: int = 30

    @property
    def api_base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    @classmethod
    def from_env(cls) -> TelegramConfig | None:
        """Build from environment variables; return None if bot token is missing."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return None

        use_webhook = os.environ.get("TELEGRAM_USE_WEBHOOK", "false").lower() == "true"
        poll_timeout = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "30"))
        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

        return cls(
            bot_token=token,
            webhook_secret=webhook_secret,
            use_webhook=use_webhook,
            poll_timeout=poll_timeout,
        )
