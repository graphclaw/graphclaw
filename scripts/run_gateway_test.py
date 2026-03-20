"""scripts/run_gateway_test.py — Test gateway with email + Telegram, no Redis needed.

Uses a ConsoleBroker that prints every inbound message to stdout so you can
verify channels are working without spinning up Redis or Docker.

Usage:
    cd C:/Users/abhis/Projects/openclawdotai
    python scripts/run_gateway_test.py

What to expect:
  - Telegram: Send a message to @graphclaw_bot → see it appear in the console
  - Email: Requires Gmail App Password (see output for instructions)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

# ── Load docker/.env ──────────────────────────────────────────────────────────
env_file = Path(__file__).parent.parent / "docker" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault("GATEWAY_ENABLED_CHANNELS", "telegram,email")

src = str(Path(__file__).parent.parent / "src")
if src not in sys.path:
    sys.path.insert(0, src)

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
# Reduce noise from httpx/asyncio internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("gateway_test")

# ── ConsoleBroker: prints every published message ─────────────────────────────
from graphclaw.infra.broker import MessageBroker  # noqa: E402


_broker_logger = logging.getLogger("inbound")


class ConsoleBroker(MessageBroker):
    """Broker that logs every published message (no Redis needed)."""

    async def publish(self, queue: str, message: str | dict) -> None:
        if isinstance(message, dict):
            payload = message
        else:
            try:
                payload = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                payload = {"raw": str(message)}

        channel = payload.get("channel", "?")
        sender = payload.get("sender", "?")
        body = payload.get("body", "")
        msg_id = payload.get("message_id", "?")

        _broker_logger.info(
            "\n  --- INBOUND MESSAGE ---\n"
            "  Queue   : %s\n"
            "  Channel : %s\n"
            "  Sender  : %s\n"
            "  Msg ID  : %s\n"
            "  Body    : %s\n"
            "  ----------------------",
            queue, channel, sender, msg_id, body[:300],
        )

    async def consume(self, queue: str, timeout: float = 5.0) -> AsyncIterator[str]:
        # ConsoleBroker never has messages to consume
        return
        yield  # make it an async generator

    async def acknowledge(self, queue: str, message_id: str) -> None:
        pass

    async def close(self) -> None:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    broker = ConsoleBroker()

    from graphclaw.gateway.channel_registry import build_registry

    channels = os.environ.get("GATEWAY_ENABLED_CHANNELS", "telegram")
    channel_list = [c.strip() for c in channels.split(",") if c.strip()]

    logger.info("Starting channel adapters: %s", channel_list)
    registry = build_registry(channel_list)
    await registry.start_all(broker)

    # Show which channels actually started
    logger.info("=" * 60)
    logger.info("GraphClaw Gateway -- Console Test Mode")
    logger.info("=" * 60)
    for name in channel_list:
        adapter = registry.get(name)
        if adapter is not None:
            configured = getattr(adapter, "_config", None) is not None
            status = "RUNNING" if configured else "SKIPPED (not configured)"
            logger.info("  %-12s %s", name, status)
    logger.info("=" * 60)

    tg_adapter = registry.get("telegram")
    if tg_adapter and getattr(tg_adapter, "_config", None):
        logger.info("Telegram is LIVE (long-poll). Send a message to @graphclaw_bot.")
    else:
        logger.info("Telegram: not configured (TELEGRAM_BOT_TOKEN missing)")

    email_adapter = registry.get("email")
    if email_adapter and getattr(email_adapter, "_poller", None):
        logger.info(
            "Email: polling %s every %ss",
            os.environ.get("GATEWAY_IMAP_USER", "?"),
            os.environ.get("GATEWAY_IMAP_POLL_INTERVAL", "30"),
        )
    else:
        imap_pass = os.environ.get("GATEWAY_IMAP_PASS", "")
        if not imap_pass or imap_pass == "REPLACE_WITH_GMAIL_APP_PASSWORD":
            logger.warning(
                "Email NOT CONFIGURED -- update GATEWAY_IMAP_PASS in docker/.env "
                "with a Gmail App Password (https://myaccount.google.com/security)"
            )

    logger.info("Press Ctrl+C to stop.")

    try:
        # Keep running indefinitely
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stopping...")
    finally:
        await registry.stop_all()
        logger.info("Gateway test runner stopped")


if __name__ == "__main__":
    asyncio.run(main())
