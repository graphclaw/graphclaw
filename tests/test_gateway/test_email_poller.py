# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.gateway.channels.email.poller — EmailPoller class."""

from __future__ import annotations

import imaplib
from collections.abc import AsyncIterator
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

from graphclaw.gateway.channels.email.poller import EmailPoller
from graphclaw.gateway.schemas import InboundMessage

# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------


class MockBroker:
    """Minimal in-memory MessageBroker stub."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, queue: str, message: str) -> None:
        self.published.append((queue, message))

    async def consume(self, queue: str) -> AsyncIterator[str]:
        return
        yield

    async def acknowledge(self, queue: str, message_id: str) -> None:
        pass

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_email_bytes(
    from_addr: str = "sender@example.com",
    subject: str = "Test",
    body: str = "Hello",
    message_id: str = "<test@example.com>",
) -> bytes:
    """Build minimal RFC 2822 email bytes."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Thu, 01 Jun 2024 12:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


def _make_poller(broker=None, poll_interval: int = 1) -> EmailPoller:
    return EmailPoller(
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password="secret",
        folder="INBOX",
        poll_interval=poll_interval,
        broker=broker,
    )


# ---------------------------------------------------------------------------
# Tests: _poll_once / _sync_poll_once
# ---------------------------------------------------------------------------


class TestPollOnceFetchesUnseen:
    async def test_poll_once_fetches_unseen(self):
        """_poll_once should call imaplib.IMAP4_SSL and search for UNSEEN."""
        raw_bytes = _make_raw_email_bytes()
        mock_imap = MagicMock(spec=imaplib.IMAP4_SSL)
        mock_imap.__enter__ = lambda s: mock_imap
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.return_value = ("OK", [b"Logged in"])
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.search.return_value = ("OK", [b"1"])
        mock_imap.fetch.return_value = (
            "OK",
            [(b"1 (RFC822 {len})", raw_bytes)],
        )
        mock_imap.store.return_value = ("OK", [b"1"])

        poller = _make_poller()

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_once()

        mock_imap.search.assert_called_once_with(None, "UNSEEN")
        mock_imap.fetch.assert_called_once_with(b"1", "(RFC822)")

    async def test_poll_once_marks_messages_as_seen(self):
        """After processing, each message should be marked \\Seen."""
        raw_bytes = _make_raw_email_bytes()
        mock_imap = MagicMock(spec=imaplib.IMAP4_SSL)
        mock_imap.__enter__ = lambda s: mock_imap
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"2"])
        mock_imap.search.return_value = ("OK", [b"1 2"])
        mock_imap.fetch.return_value = (
            "OK",
            [(b"1 (RFC822 {len})", raw_bytes)],
        )
        mock_imap.store.return_value = ("OK", [])

        poller = _make_poller()

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_once()

        # store should be called for each message number
        assert mock_imap.store.call_count == 2
        mock_imap.store.assert_any_call(b"1", "+FLAGS", "\\Seen")
        mock_imap.store.assert_any_call(b"2", "+FLAGS", "\\Seen")


class TestPollOncePublishesToBroker:
    async def test_poll_once_publishes_to_broker(self):
        """Normalized messages should be published to INBOUND_MESSAGES queue."""
        raw_bytes = _make_raw_email_bytes(
            from_addr="alice@example.com",
            subject="Important",
            message_id="<pub-test@example.com>",
        )
        mock_imap = MagicMock(spec=imaplib.IMAP4_SSL)
        mock_imap.__enter__ = lambda s: mock_imap
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.search.return_value = ("OK", [b"1"])
        mock_imap.fetch.return_value = (
            "OK",
            [(b"1 (RFC822)", raw_bytes)],
        )
        mock_imap.store.return_value = ("OK", [])

        broker = MockBroker()
        poller = _make_poller(broker=broker)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_once()

        assert len(broker.published) == 1
        queue, message_json = broker.published[0]
        from graphclaw.infra.broker import INBOUND_MESSAGES

        assert queue == INBOUND_MESSAGES
        restored = InboundMessage.model_validate_json(message_json)
        assert restored.sender == "alice@example.com"
        assert restored.subject == "Important"
        assert restored.message_id == "pub-test@example.com"

    async def test_poll_once_no_unseen_messages(self):
        """When no unseen messages, broker should not be called."""
        mock_imap = MagicMock(spec=imaplib.IMAP4_SSL)
        mock_imap.__enter__ = lambda s: mock_imap
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"0"])
        mock_imap.search.return_value = ("OK", [b""])
        mock_imap.store.return_value = ("OK", [])

        broker = MockBroker()
        poller = _make_poller(broker=broker)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_once()

        assert len(broker.published) == 0
        mock_imap.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: backoff on connection error
# ---------------------------------------------------------------------------


class TestBackoffOnConnectionError:
    async def test_backoff_on_connection_error(self):
        """start() should increase _backoff on consecutive failures."""
        poller = _make_poller(poll_interval=0)

        call_count = 0

        async def failing_poll_once():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                poller._running = False
            raise ConnectionError("IMAP connection refused")

        poller._poll_once = failing_poll_once  # type: ignore[method-assign]

        initial_backoff = poller._backoff
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await poller.start()

        # Back-off should have doubled at least once
        assert poller._backoff > initial_backoff

    async def test_backoff_resets_on_success(self):
        """Back-off should reset to 1.0 after a successful poll."""
        poller = _make_poller(poll_interval=0)
        poller._backoff = 64.0  # Simulate high backoff from previous errors

        call_count = 0

        async def successful_poll_once():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                poller._running = False

        poller._poll_once = successful_poll_once  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await poller.start()

        assert poller._backoff == 1.0


# ---------------------------------------------------------------------------
# Tests: stop exits loop
# ---------------------------------------------------------------------------


class TestStopExitsLoop:
    async def test_stop_exits_loop(self):
        """stop() should cause start() to exit after the current iteration."""
        poller = _make_poller(poll_interval=0)

        async def poll_and_stop():
            await poller.stop()

        poller._poll_once = poll_and_stop  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await poller.start()

        assert not poller._running

    async def test_stop_sets_running_false(self):
        poller = _make_poller()
        assert not poller._running
        poller._running = True
        await poller.stop()
        assert not poller._running
