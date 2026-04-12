"""graphclaw.agent.outbound — OutboundDispatcher: routes agent-initiated messages to channels.

Description
-----------
``OutboundDispatcher`` is the single egress point for all agent-initiated
outbound communication.  It supports two transport paths:

1. **Direct channel dispatch** — when channel adapters (EmailSender,
   TelegramSender) are injected at construction time, messages are sent
   immediately via the adapter.
2. **Broker queue dispatch** — when a ``MessageBroker`` is provided the
   message is published to the ``OUTBOUND_MESSAGES`` queue, which is
   consumed by the gateway's existing ``EmailSender.start_consumer()`` loop.

The dispatcher resolves the correct channel for a given user by consulting
the user's ``UserNode.preferences.channels`` list in the graph, or falls back
to the channels explicitly registered at construction time.

Design Patterns
---------------
- Strategy: Channel selection is resolved at send time based on the ``channel``
  parameter or the user's registered preferences, making it trivial to add
  new channels (WhatsApp, Slack, etc.) without changing calling code.
- Dependency Injection: All adapters and the broker are injected; no global
  state.
- Graceful Degradation: If a channel adapter is unavailable the dispatcher
  logs a warning and skips that channel rather than raising.

Public API
----------
- OutboundDispatcher: Main dispatcher class.
  - send_email: Send an email message.
  - send_telegram: Send a Telegram message.
  - send: Route to the correct channel adapter based on ``channel`` argument.
  - broadcast: Send the same message across *all* configured channels.

Dependencies
------------
- graphclaw.gateway.channels.email.sender: EmailSender.
- graphclaw.gateway.channels.telegram.sender: TelegramSender.
- graphclaw.gateway.channels.telegram.config: TelegramConfig.
- graphclaw.infra.broker: MessageBroker, OUTBOUND_MESSAGES.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.gateway.channels.email.sender import EmailSender
    from graphclaw.gateway.channels.telegram.sender import TelegramSender
    from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class OutboundDispatcher:
    """Routes agent-initiated messages to the correct channel adapter.

    Parameters
    ----------
    email_sender:
        Optional :class:`~graphclaw.gateway.channels.email.sender.EmailSender`
        for direct SMTP dispatch.
    telegram_sender:
        Optional :class:`~graphclaw.gateway.channels.telegram.sender.TelegramSender`
        for direct Telegram Bot API dispatch.
    broker:
        Optional :class:`~graphclaw.infra.broker.MessageBroker` for queue-based
        dispatch (used when direct adapters are not injected).
    from_email:
        The "From" address used when sending email (e.g. the agent's gmail address).
    """

    def __init__(
        self,
        email_sender: EmailSender | None = None,
        telegram_sender: TelegramSender | None = None,
        broker: MessageBroker | None = None,
        from_email: str | None = None,
    ) -> None:
        self._email = email_sender
        self._telegram = telegram_sender
        self._broker = broker
        self._from_email = from_email or os.environ.get("GATEWAY_SMTP_USER", "")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> None:
        """Send an email message.

        Tries direct EmailSender first; falls back to OUTBOUND_MESSAGES queue.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        body:
            Plain-text email body.
        in_reply_to:
            Optional Message-ID of the email being replied to.
        """
        if self._email is not None:
            try:
                await self._email.send(
                    recipient=to,
                    subject=subject,
                    body=body,
                    in_reply_to=in_reply_to,
                )
                logger.info("OutboundDispatcher: email sent to %s via direct adapter", to)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OutboundDispatcher: direct email send failed: %s — falling back to queue", exc
                )

        if self._broker is not None:
            from graphclaw.infra.broker import OUTBOUND_MESSAGES

            payload = json.dumps(
                {
                    "channel": "email",
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "in_reply_to": in_reply_to,
                }
            )
            await self._broker.publish(OUTBOUND_MESSAGES, payload)
            logger.info("OutboundDispatcher: email queued for %s via broker", to)
            return

        logger.warning("OutboundDispatcher: no email adapter or broker — email to %s dropped", to)

    async def send_telegram(self, chat_id: str, text: str) -> None:
        """Send a Telegram message to *chat_id*.

        Parameters
        ----------
        chat_id:
            Telegram chat ID (numeric string or @username).
        text:
            Message text (max 4096 chars).
        """
        if self._telegram is not None:
            try:
                await self._telegram.send(chat_id=chat_id, text=text)
                logger.info("OutboundDispatcher: telegram sent to chat_id=%s", chat_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutboundDispatcher: telegram send failed: %s", exc)
                return

        if self._broker is not None:
            from graphclaw.infra.broker import OUTBOUND_MESSAGES

            payload = json.dumps({"channel": "telegram", "chat_id": chat_id, "text": text})
            await self._broker.publish(OUTBOUND_MESSAGES, payload)
            logger.info("OutboundDispatcher: telegram queued for chat_id=%s via broker", chat_id)
            return

        logger.warning(
            "OutboundDispatcher: no telegram adapter or broker — message to %s dropped", chat_id
        )

    async def send(
        self,
        channel: str,
        *,
        to: str,
        subject: str = "",
        body: str,
    ) -> None:
        """Route a message to the specified channel.

        Parameters
        ----------
        channel:
            One of ``"email"`` or ``"telegram"``.
        to:
            Recipient address: email address for ``"email"``, chat_id for
            ``"telegram"``.
        subject:
            Subject line (email only; ignored for Telegram).
        body:
            Message content.
        """
        if channel == "email":
            await self.send_email(to=to, subject=subject, body=body)
        elif channel == "telegram":
            await self.send_telegram(chat_id=to, text=body)
        else:
            logger.warning("OutboundDispatcher.send: unknown channel %r — message dropped", channel)

    async def broadcast(
        self,
        channels: list[dict[str, Any]],
        subject: str = "",
        body: str = "",
    ) -> None:
        """Send the same message across multiple channels.

        Parameters
        ----------
        channels:
            List of channel descriptors, e.g.
            ``[{"channel": "email", "to": "user@example.com"},
               {"channel": "telegram", "to": "12345678"}]``.
        subject:
            Subject line (email only).
        body:
            Message content.
        """
        for ch in channels:
            await self.send(
                ch.get("channel", ""),
                to=ch.get("to", ""),
                subject=subject,
                body=body,
            )

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, broker: MessageBroker | None = None) -> OutboundDispatcher:
        """Construct an OutboundDispatcher wired from environment variables.

        Creates an ``EmailSender`` from SMTP env vars and a ``TelegramSender``
        from the bot token env var.  Both are optional — missing credentials
        cause the respective adapter to be omitted and messages fall back to
        broker queue dispatch.

        Parameters
        ----------
        broker:
            Optional MessageBroker; used as queue fallback when direct adapter
            send fails.
        """
        email_sender = None
        telegram_sender = None

        # Email sender
        smtp_user = os.environ.get("GATEWAY_SMTP_USER", "")
        smtp_pass = os.environ.get("GATEWAY_SMTP_PASS", "")
        smtp_host = os.environ.get("GATEWAY_SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("GATEWAY_SMTP_PORT", "587"))
        if smtp_user and smtp_pass:
            try:
                from graphclaw.gateway.channels.email.sender import EmailSender  # noqa: PLC0415

                # Port 587 uses STARTTLS; port 465 uses implicit TLS (SMTPS).
                use_tls = smtp_port == 465
                start_tls = smtp_port == 587
                email_sender = EmailSender(
                    host=smtp_host,
                    port=smtp_port,
                    username=smtp_user,
                    password=smtp_pass,
                    use_tls=use_tls,
                    start_tls=start_tls,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutboundDispatcher.from_env: could not create EmailSender: %s", exc)

        # Telegram sender
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if bot_token:
            try:
                from graphclaw.gateway.channels.telegram.config import (
                    TelegramConfig,  # noqa: PLC0415
                )
                from graphclaw.gateway.channels.telegram.sender import (
                    TelegramSender,  # noqa: PLC0415
                )

                tg_config = TelegramConfig(bot_token=bot_token)
                telegram_sender = TelegramSender(config=tg_config)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OutboundDispatcher.from_env: could not create TelegramSender: %s", exc
                )

        return cls(
            email_sender=email_sender,
            telegram_sender=telegram_sender,
            broker=broker,
            from_email=smtp_user,
        )


__all__ = ["OutboundDispatcher"]
