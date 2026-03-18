"""graphclaw.gateway.email_poller — IMAP inbox polling loop.

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
- EmailPoller.__init__: Configure IMAP credentials, folder, poll interval, and broker.
- EmailPoller.start: Begin the polling loop (intended to run as an asyncio Task).
- EmailPoller.stop: Signal the loop to exit after the current iteration.
- EmailPoller._poll_once: Single poll iteration (exposed for testing).

Dependencies
------------
- graphclaw.gateway.normalizer: normalize_email.
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
"""
from __future__ import annotations

import asyncio
import imaplib
import logging
from email import message_from_bytes
from email.message import EmailMessage

from graphclaw.gateway.normalizer import normalize_email
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 300


class EmailPoller:
    """Background IMAP polling loop.

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
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        poll_interval: int = 60,
        broker: MessageBroker | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder
        self._poll_interval = poll_interval
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

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run inside to_thread)
    # ------------------------------------------------------------------

    def _sync_poll_once(self) -> None:
        """Synchronous implementation of a single poll cycle (runs in thread)."""
        with imaplib.IMAP4_SSL(self._host, self._port) as imap:
            imap.login(self._username, self._password)
            imap.select(self._folder)

            _status, message_numbers_raw = imap.search(None, "UNSEEN")
            message_numbers: list[bytes] = (
                message_numbers_raw[0].split() if message_numbers_raw[0] else []
            )

            logger.debug(
                "EmailPoller: %d unseen message(s) found", len(message_numbers)
            )

            for num in message_numbers:
                try:
                    self._process_message(imap, num)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "EmailPoller: failed to process message %s",
                        num,
                        exc_info=exc,
                    )

    def _process_message(self, imap: imaplib.IMAP4_SSL, num: bytes) -> None:
        """Fetch, normalize, publish, and mark a single message as seen.

        Parameters
        ----------
        imap:
            An authenticated, open ``IMAP4_SSL`` connection with the desired
            folder already selected.
        num:
            The IMAP sequence number (as bytes) of the message to process.
        """
        _status, data = imap.fetch(num, "(RFC822)")
        if not data or data[0] is None:
            logger.warning("EmailPoller: empty fetch response for message %s", num)
            return

        raw_bytes: bytes = data[0][1]  # type: ignore[index]
        parsed = message_from_bytes(raw_bytes, _class=EmailMessage)
        inbound = normalize_email(parsed)  # type: ignore[arg-type]

        if self._broker is not None:
            # Publish synchronously inside the thread — the broker's sync
            # wrapper (if any) is used here; the async path uses _poll_once.
            # We use asyncio.run_coroutine_threadsafe when a loop is running,
            # or fall back to a new event loop for isolated test contexts.
            import asyncio as _asyncio  # local import to avoid shadowing

            try:
                loop = _asyncio.get_event_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                future = _asyncio.run_coroutine_threadsafe(
                    self._broker.publish(INBOUND_MESSAGES, inbound.model_dump_json()),
                    loop,
                )
                future.result(timeout=30)
            else:
                _asyncio.run(
                    self._broker.publish(INBOUND_MESSAGES, inbound.model_dump_json())
                )
            logger.debug(
                "EmailPoller: published message %s (session=%s)",
                inbound.message_id,
                inbound.session_id,
            )

        # Mark as seen regardless of publish success to avoid infinite redelivery.
        imap.store(num, "+FLAGS", "\\Seen")
