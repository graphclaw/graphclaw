"""graphclaw.gateway.email — Unified email channel: IMAP polling and SMTP sending.

Description
-----------
Provides a single-module facade for the email channel that bundles:

- ``EmailConfig`` — Pydantic configuration model for IMAP and SMTP credentials
  (re-exported from ``graphclaw.gateway.models`` for convenience).
- ``EmailPoller`` — Background async task that polls an IMAP inbox, normalises
  each unseen message to ``InboundMessage``, and publishes it to the broker.
- ``send_email`` — Thin async helper that sends a single outbound email via
  SMTP using ``aiosmtplib``.

Both IMAP polling (``imaplib``) and SMTP sending (``aiosmtplib``) are executed
asynchronously: blocking ``imaplib`` calls are offloaded to a thread pool via
``asyncio.to_thread``; SMTP sending uses the native async ``aiosmtplib`` API.

Design Patterns
---------------
- Facade: This module exposes a simplified surface (``EmailPoller``, ``send_email``,
  ``EmailConfig``) that hides the underlying split between ``email_poller.py``
  and ``email_sender.py``.
- Adapter: ``EmailPoller`` adapts synchronous ``imaplib`` I/O to the async
  broker queue abstraction.
- Configuration Object: ``EmailConfig`` encapsulates all credentials and tuning
  parameters so callers never pass individual positional arguments.

Public API
----------
- EmailConfig: Pydantic configuration model for IMAP/SMTP credentials.
- EmailPoller: Async IMAP polling loop that publishes to the broker.
- EmailPoller.start: Begin the polling loop (run as an asyncio Task).
- EmailPoller.stop: Signal the loop to exit after the current iteration.
- EmailPoller._poll_inbox: Execute one IMAP poll cycle; returns list of messages.
- send_email: Send a single outbound email via SMTP.

Dependencies
------------
- graphclaw.gateway.models: EmailConfig.
- graphclaw.gateway.normalizer: normalize_email.
- graphclaw.gateway.schemas: InboundMessage, OutboundMessage.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES.
- asyncio: to_thread, sleep (stdlib).
- imaplib: IMAP4_SSL (stdlib).
- email: message_from_bytes, EmailMessage (stdlib).
- aiosmtplib: Async SMTP client (third-party).
- logging: Structured logging.

Notes
-----
``EmailPoller._poll_inbox`` is intentionally exposed (not private) so that
unit tests can call it directly without starting the full polling loop.

``send_email`` is a free function rather than a class method to keep the
sending path stateless and easily mockable in tests.
"""
from __future__ import annotations

import asyncio
import imaplib
import logging
import uuid
from email.message import EmailMessage as _StdEmailMessage
from email.message import EmailMessage
from email import message_from_bytes

import aiosmtplib

from graphclaw.gateway.models import EmailConfig
from graphclaw.gateway.normalizer import normalize_email
from graphclaw.gateway.schemas import InboundMessage, OutboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS: float = 300.0


class EmailPoller:
    """Background IMAP polling loop.

    Polls an IMAP inbox for unseen messages on a configurable interval,
    normalises each message to ``InboundMessage`` via ``normalize_email``,
    and publishes it to the broker's ``INBOUND_MESSAGES`` queue.

    Parameters
    ----------
    config:
        ``EmailConfig`` containing IMAP host, port, credentials, and
        ``poll_interval``.
    broker:
        ``MessageBroker`` instance used to publish inbound messages.
        When ``None``, messages are normalised but not published (useful
        for testing the normalisation path in isolation).
    """

    def __init__(
        self,
        config: EmailConfig,
        broker: MessageBroker | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._running = False
        self._backoff: float = 1.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the IMAP polling loop.

        Runs until ``stop()`` is called.  Should be launched as an asyncio
        Task (e.g. ``asyncio.create_task(poller.start())``).

        Errors during a poll cycle are caught, logged, and cause an
        exponential back-off (doubling each failure, capped at 300 s) before
        retrying.  Back-off resets to 1 second on a successful iteration.
        """
        self._running = True
        logger.info(
            "EmailPoller starting",
            extra={
                "imap_host": self._config.imap_host,
                "poll_interval": self._config.poll_interval,
            },
        )
        while self._running:
            try:
                await self._poll_inbox()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EmailPoller error — retrying after back-off",
                    exc_info=exc,
                    extra={"backoff_seconds": self._backoff},
                )
                await asyncio.sleep(min(self._backoff, _MAX_BACKOFF_SECONDS))
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF_SECONDS)
            else:
                self._backoff = 1.0
            if self._running:
                await asyncio.sleep(self._config.poll_interval)
        logger.info("EmailPoller stopped")

    async def stop(self) -> None:
        """Signal the polling loop to stop after the current iteration."""
        logger.info("EmailPoller stop requested")
        self._running = False

    async def _poll_inbox(self) -> list[InboundMessage]:
        """Execute one IMAP poll cycle.

        Fetches all unseen messages from the configured IMAP inbox, normalises
        each to an ``InboundMessage``, publishes to the broker (if configured),
        and marks each message as ``\\Seen``.

        All ``imaplib`` calls run inside ``asyncio.to_thread`` to avoid
        blocking the event loop.

        Returns
        -------
        list[InboundMessage]:
            Normalised messages from this poll cycle.
        """
        return await asyncio.to_thread(self._sync_poll_inbox)

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run inside to_thread)
    # ------------------------------------------------------------------

    def _sync_poll_inbox(self) -> list[InboundMessage]:
        """Synchronous IMAP poll implementation (runs inside a thread pool)."""
        messages: list[InboundMessage] = []

        with imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port) as imap:
            imap.login(self._config.username, self._config.password)
            imap.select("INBOX")

            _status, message_numbers_raw = imap.search(None, "UNSEEN")
            message_numbers: list[bytes] = (
                message_numbers_raw[0].split() if message_numbers_raw[0] else []
            )

            logger.debug("EmailPoller: %d unseen message(s) found", len(message_numbers))

            for num in message_numbers:
                try:
                    msg = self._fetch_and_normalize(imap, num)
                    if msg is not None:
                        messages.append(msg)
                        self._publish_sync(msg)
                    # Mark as seen regardless of publish success
                    imap.store(num, "+FLAGS", "\\Seen")
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "EmailPoller: failed to process message %s",
                        num,
                        exc_info=exc,
                    )

        return messages

    def _fetch_and_normalize(
        self, imap: imaplib.IMAP4_SSL, num: bytes
    ) -> InboundMessage | None:
        """Fetch a single message and normalise it to ``InboundMessage``."""
        _status, data = imap.fetch(num, "(RFC822)")
        if not data or data[0] is None:
            logger.warning("EmailPoller: empty fetch response for message %s", num)
            return None
        raw_bytes: bytes = data[0][1]  # type: ignore[index]
        parsed = message_from_bytes(raw_bytes, _class=EmailMessage)
        return normalize_email(parsed)  # type: ignore[arg-type]

    def _publish_sync(self, message: InboundMessage) -> None:
        """Publish a normalised message to the broker (sync, inside thread)."""
        if self._broker is None:
            return

        import asyncio as _asyncio  # local import to avoid name shadowing

        try:
            loop = _asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            future = _asyncio.run_coroutine_threadsafe(
                self._broker.publish(INBOUND_MESSAGES, message.model_dump_json()),
                loop,
            )
            future.result(timeout=30)
        else:
            _asyncio.run(
                self._broker.publish(INBOUND_MESSAGES, message.model_dump_json())
            )

        logger.debug(
            "EmailPoller: published message %s (session=%s)",
            message.message_id,
            message.session_id,
        )


# ---------------------------------------------------------------------------
# SMTP send helper
# ---------------------------------------------------------------------------


async def send_email(
    message: OutboundMessage,
    config: EmailConfig,
) -> None:
    """Send a single outbound email via SMTP using ``aiosmtplib``.

    Parameters
    ----------
    message:
        ``OutboundMessage`` to deliver.  Only ``channel="email"`` messages
        are handled; others raise ``ValueError``.
    config:
        ``EmailConfig`` containing SMTP host, port, and credentials.

    Raises
    ------
    ValueError
        If ``message.channel`` is not ``"email"``.
    aiosmtplib.SMTPException
        If SMTP delivery fails.
    """
    if message.channel != "email":
        raise ValueError(
            f"send_email only handles channel='email', got {message.channel!r}"
        )

    msg = _StdEmailMessage()
    msg["From"] = config.username
    msg["To"] = message.recipient
    msg["Subject"] = message.subject
    msg["Message-ID"] = f"<{uuid.uuid4()}@graphclaw>"
    if message.in_reply_to:
        clean_id = message.in_reply_to.strip("<>")
        msg["In-Reply-To"] = f"<{clean_id}>"
        msg["References"] = f"<{clean_id}>"
    msg.set_content(message.body)

    logger.info(
        "send_email: sending",
        extra={"recipient": message.recipient, "subject": message.subject},
    )
    await aiosmtplib.send(
        msg,
        hostname=config.smtp_host,
        port=config.smtp_port,
        username=config.username,
        password=config.password,
        use_tls=(config.smtp_port == 465),
    )
    logger.debug("send_email: delivered to %s", message.recipient)
