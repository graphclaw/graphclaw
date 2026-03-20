"""graphclaw.gateway.channels.email.sender — SMTP outbound email delivery.

Description
-----------
Provides ``EmailSender``, which handles outbound email delivery over SMTP/TLS
using ``aiosmtplib``, and also exposes ``start_consumer``, a method that
continuously drains the broker's ``OUTBOUND_MESSAGES`` queue and calls ``send``
for each ``channel="email"`` message.

Design Patterns
---------------
- Adapter: Bridges the broker's queue-based ``OutboundMessage`` DTO to the SMTP
  wire protocol.
- Consumer Loop: ``start_consumer`` implements a long-running async iterator
  over the broker queue, decoupling the sender from the caller's event loop.
- Dependency Injection: The broker is injected into ``start_consumer`` rather
  than stored at construction time, making the sender usable both as a direct
  ``send`` helper and as a queue consumer.

Public API
----------
- EmailSender.__init__: Configure SMTP credentials and TLS preference.
- EmailSender.send: Send a single email asynchronously.
- EmailSender.start_consumer: Consume ``OUTBOUND_MESSAGES`` queue and send emails.

Dependencies
------------
- graphclaw.gateway.schemas: OutboundMessage.
- graphclaw.infra.broker: MessageBroker, OUTBOUND_MESSAGES.
- aiosmtplib: Async SMTP client (third-party, must be installed).
- email.message: EmailMessage (stdlib).
- logging: structured logging.

Notes
-----
``aiosmtplib`` must be listed as a project dependency (handled by WS-I).
TLS is controlled by ``use_tls``; set to ``False`` for plaintext SMTP (port 25)
or STARTTLS scenarios — though STARTTLS is not explicitly handled here.

If the broker yields a message for a non-email channel (``msg.channel != "email"``)
it is silently skipped by ``start_consumer``; routing to other senders is the
responsibility of the channel router (a future component).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

import aiosmtplib

from graphclaw.gateway.schemas import OutboundMessage
from graphclaw.infra.broker import OUTBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)


class EmailSender:
    """Async SMTP email sender and outbound queue consumer.

    Parameters
    ----------
    host:
        SMTP server hostname (e.g. ``"smtp.gmail.com"``).
    port:
        SMTP server port (typically 465 for implicit TLS, 587 for STARTTLS).
    username:
        SMTP login username / email address.
    password:
        SMTP login password or app-specific password.
    use_tls:
        Whether to use implicit TLS (``SMTPS``). Defaults to ``True``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> None:
        """Send a single email via SMTP.

        Parameters
        ----------
        recipient:
            Target email address.
        subject:
            Email subject line.
        body:
            Plain-text email body.
        in_reply_to:
            Optional ``Message-ID`` of the email being replied to.
            When provided, the ``In-Reply-To`` and ``References`` headers
            are set for proper threading.
        """
        msg = EmailMessage()
        msg["From"] = self._username
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{uuid.uuid4()}@graphclaw>"
        if in_reply_to:
            clean_id = in_reply_to.strip("<>")
            msg["In-Reply-To"] = f"<{clean_id}>"
            msg["References"] = f"<{clean_id}>"
        msg.set_content(body)

        logger.info(
            "EmailSender: sending email",
            extra={"recipient": recipient, "subject": subject},
        )
        await aiosmtplib.send(
            msg,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            use_tls=self._use_tls,
        )
        logger.debug("EmailSender: sent to %s", recipient)

    async def start_consumer(self, broker: MessageBroker) -> None:
        """Consume the ``OUTBOUND_MESSAGES`` queue and send emails.

        Runs until the broker's ``consume`` iterator is exhausted or an
        unhandled exception propagates.  Individual send failures are logged
        and the consumer continues to the next message.

        Parameters
        ----------
        broker:
            ``MessageBroker`` instance to consume from.
        """
        logger.info("EmailSender consumer starting")
        async for message_json in broker.consume(OUTBOUND_MESSAGES):
            try:
                msg = OutboundMessage.model_validate_json(message_json)
                if msg.channel != "email":
                    logger.debug(
                        "EmailSender: skipping non-email channel message %s (channel=%s)",
                        msg.message_id,
                        msg.channel,
                    )
                    continue
                await self.send(
                    recipient=msg.recipient,
                    subject=msg.subject,
                    body=msg.body,
                    in_reply_to=msg.in_reply_to,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EmailSender: failed to send message",
                    exc_info=exc,
                )
        logger.info("EmailSender consumer stopped")
