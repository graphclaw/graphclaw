# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channels.email.poller — IMAP inbox polling loop.

Description
-----------
Provides ``EmailPoller``, a background task that periodically connects to an
IMAP server, fetches unseen messages, normalizes each one into an
``InboundMessage`` via ``normalize_email``, publishes it to the broker's
``INBOUND_MESSAGES`` queue, then marks the message as seen on the server.

The synchronous ``imaplib`` calls are offloaded to a thread pool via
``asyncio.to_thread`` so the event loop is never blocked.  An exponential
back-off (capped at 300 s) handles transient connection failures gracefully.

Design Patterns
---------------
- Background Task: ``start()`` is designed to run as a ``asyncio`` task; it
  loops until ``stop()`` sets ``_running = False``.
- Adapter: Bridges the synchronous ``imaplib`` interface to the async broker.
- Exponential Back-off: Doubles the sleep interval on each consecutive failure,
  capping at 300 seconds.

Public API
----------
- EmailPoller.__init__: Configure IMAP credentials via explicit args or EmailConfig.
- EmailPoller.start: Begin the polling loop (intended to run as an asyncio Task).
- EmailPoller.stop: Signal the loop to exit after the current iteration.
- EmailPoller._poll_once: Single poll iteration (exposed for testing).
- EmailPoller._poll_inbox: Config-based poll cycle returning list of InboundMessages.

Dependencies
------------
- graphclaw.gateway.channels.email.normalizer: normalize_email.
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES.
- asyncio: event loop and thread offloading (stdlib).
- imaplib: IMAP4_SSL (stdlib).
- email: message_from_bytes, EmailMessage (stdlib).
- logging: structured logging.

Notes
-----
Only ``text/plain`` bodies are extracted; HTML-only emails fall back gracefully
to empty body (see ``normalizer._extract_body``).

The poller uses ``SEARCH UNSEEN`` so already-processed messages are never
re-fetched across restarts, provided the ``\\Seen`` flag is set successfully.
If the broker publish fails the message is still marked seen to avoid infinite
redelivery — operators should monitor DLQ / broker errors separately.

``EmailPoller`` supports two construction modes:
1. Explicit keyword args: ``EmailPoller(host=..., port=..., username=..., password=...)``
2. Config object: ``EmailPoller(config=EmailConfig(...))``

The config-based mode is provided for backward compatibility with code that used
the ``EmailPoller`` from ``graphclaw.gateway.email``.
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
from email import message_from_bytes
from email.message import EmailMessage

from graphclaw.gateway.channels.email.config import EmailConfig
from graphclaw.gateway.channels.email.normalizer import normalize_email
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 300


class EmailPoller:
    """Background IMAP polling loop.

    Supports two construction modes:

    1. Explicit keyword args (primary interface for the adapter)::

        EmailPoller(host="imap.example.com", port=993, username="u", password="p")

    2. Config object (backward-compatible interface for tests and legacy code)::

        EmailPoller(config=EmailConfig(...))

    Parameters
    ----------
    host:
        IMAP server hostname (e.g. ``"imap.gmail.com"``).
    port:
        IMAP server port (typically 993 for IMAP over TLS).
    username:
        IMAP login username / email address.
    password:
        IMAP login password or app-specific password.
    folder:
        Mailbox folder to poll. Defaults to ``"INBOX"``.
    poll_interval:
        Seconds to wait between successful poll cycles. Defaults to 60.
    broker:
        ``MessageBroker`` instance used to publish inbound messages.
        If ``None``, messages are normalized but not published (useful for
        testing the normalization path in isolation).
    config:
        ``EmailConfig`` instance. When provided, takes precedence over
        individual keyword arguments for host, port, username, password,
        and poll_interval.
    """

    def __init__(
        self,
        host: str = "",
        port: int = 993,
        username: str = "",
        password: str = "",
        folder: str = "INBOX",
        poll_interval: int = 60,
        broker: MessageBroker | None = None,
        config: EmailConfig | None = None,
    ) -> None:
        if config is not None:
            self._host = config.imap_host
            self._port = config.imap_port
            self._username = config.username
            self._password = config.password
            self._poll_interval = int(config.poll_interval)
        else:
            self._host = host
            self._port = port
            self._username = username
            self._password = password
            self._poll_interval = poll_interval
        self._folder = folder
        self._broker = broker
        self._running = False
        self._backoff: float = 1.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the polling loop.

        Runs until ``stop()`` is called.  Should be launched as an asyncio
        Task (e.g. ``asyncio.create_task(poller.start())``).
        Errors are caught, logged, and cause an exponential back-off before
        retrying. The back-off resets to 1 second on a successful iteration.
        """
        self._running = True
        logger.info(
            "EmailPoller starting",
            extra={
                "host": self._host,
                "folder": self._folder,
                "poll_interval": self._poll_interval,
            },
        )
        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EmailPoller error — will retry after back-off",
                    exc_info=exc,
                    extra={"backoff_seconds": self._backoff},
                )
                await asyncio.sleep(min(self._backoff, _MAX_BACKOFF_SECONDS))
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF_SECONDS)
            else:
                self._backoff = 1.0
            if self._running:
                await asyncio.sleep(self._poll_interval)
        logger.info("EmailPoller stopped")

    async def stop(self) -> None:
        """Signal the polling loop to stop after the current iteration."""
        logger.info("EmailPoller stop requested")
        self._running = False

    async def _poll_once(self) -> None:
        """Execute one IMAP poll cycle.

        1. Connects to the IMAP server using TLS.
        2. Selects the configured folder.
        3. Searches for unseen messages.
        4. For each unseen message: fetches raw bytes, parses, normalizes, and
           publishes to the broker queue.
        5. Marks each successfully processed message as ``\\Seen``.

        All ``imaplib`` calls are executed via ``asyncio.to_thread`` to avoid
        blocking the event loop.
        """
        await asyncio.to_thread(self._sync_poll_once)

    async def _poll_inbox(self) -> list[InboundMessage]:
        """Execute one IMAP poll cycle and return the normalized messages.

        Backward-compatible alias for ``_poll_once`` that returns the list of
        ``InboundMessage`` objects produced during the cycle.  Used by tests
        and legacy code that expect ``EmailPoller._poll_inbox()``.

        Returns
        -------
        list[InboundMessage]:
            Normalised messages from this poll cycle.
        """
        return await asyncio.to_thread(self._sync_poll_inbox_list)

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run inside to_thread)
    # ------------------------------------------------------------------

    def _sync_poll_once(self) -> None:
        """Synchronous implementation of a single poll cycle (runs in thread)."""
        self._sync_poll_inbox_list()

    def _sync_poll_inbox_list(self) -> list[InboundMessage]:
        """Synchronous poll cycle that collects and returns normalized messages.

        Used by ``_poll_inbox`` to return results to callers.  Shares logic
        with ``_sync_poll_once`` to avoid duplication.

        Returns
        -------
        list[InboundMessage]:
            Normalized messages from this poll cycle.
        """
        messages: list[InboundMessage] = []
        with imaplib.IMAP4_SSL(self._host, self._port) as imap:
            imap.login(self._username, self._password)
            imap.select(self._folder)

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

    def _fetch_and_normalize(self, imap: imaplib.IMAP4_SSL, num: bytes) -> InboundMessage | None:
        """Fetch a single message and normalize it to ``InboundMessage``.

        Parameters
        ----------
        imap:
            An authenticated, open ``IMAP4_SSL`` connection with the desired
            folder already selected.
        num:
            The IMAP sequence number (as bytes) of the message to process.

        Returns
        -------
        InboundMessage | None:
            Normalized message, or ``None`` if the fetch returned no data.
        """
        _status, data = imap.fetch(num, "(RFC822)")
        if not data or data[0] is None:
            logger.warning("EmailPoller: empty fetch response for message %s", num)
            return None
        raw_bytes: bytes = data[0][1]  # type: ignore[index]
        parsed = message_from_bytes(raw_bytes, _class=EmailMessage)
        return normalize_email(parsed)  # type: ignore[arg-type]

    def _publish_sync(self, message: InboundMessage) -> None:
        """Publish a normalized message to the broker (sync, inside thread)."""
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
            _asyncio.run(self._broker.publish(INBOUND_MESSAGES, message.model_dump_json()))

        logger.debug(
            "EmailPoller: published message %s (session=%s)",
            message.message_id,
            message.session_id,
        )
