# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.gateway.channels.email — EmailPoller and send_email.

All IMAP and SMTP interactions are mocked so no real network connections
are required.
"""

from __future__ import annotations

import imaplib
from collections.abc import AsyncIterator
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.gateway.channels.email.config import EmailConfig
from graphclaw.gateway.channels.email.poller import EmailPoller
from graphclaw.gateway.channels.email.sender import EmailSender


async def send_email(message, config) -> None:
    """Inline wrapper replacing the deleted gateway.email shim."""
    if message.channel != "email":
        raise ValueError(f"send_email only handles channel='email', got {message.channel!r}")
    sender = EmailSender(
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.username,
        password=config.password,
        use_tls=(config.smtp_port == 465),
    )
    await sender.send(
        recipient=message.recipient,
        subject=message.subject,
        body=message.body,
        in_reply_to=message.in_reply_to,
    )


from datetime import datetime, timezone

from graphclaw.gateway.schemas import InboundMessage, OutboundMessage

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


def _make_config(**overrides) -> EmailConfig:
    defaults = dict(
        imap_host="imap.example.com",
        imap_port=993,
        smtp_host="smtp.example.com",
        smtp_port=587,
        username="user@example.com",
        password="secret",
        poll_interval=1.0,
        enabled=True,
    )
    defaults.update(overrides)
    return EmailConfig(**defaults)


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


def _make_mock_imap(
    unseen_nums: bytes = b"1",
    raw_bytes: bytes | None = None,
) -> MagicMock:
    """Build a configured ``imaplib.IMAP4_SSL`` mock."""
    if raw_bytes is None:
        raw_bytes = _make_raw_email_bytes()

    mock_imap = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.__enter__ = lambda s: mock_imap
    mock_imap.__exit__ = MagicMock(return_value=False)
    mock_imap.login.return_value = ("OK", [b"Logged in"])
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [unseen_nums])
    mock_imap.fetch.return_value = (
        "OK",
        [(b"1 (RFC822)", raw_bytes)],
    )
    mock_imap.store.return_value = ("OK", [b""])
    return mock_imap


# ---------------------------------------------------------------------------
# EmailConfig tests
# ---------------------------------------------------------------------------


class TestEmailConfigDefaults:
    def test_email_config_defaults(self):
        config = EmailConfig()
        assert config.imap_host == ""
        assert config.imap_port == 993
        assert config.smtp_host == ""
        assert config.smtp_port == 587
        assert config.username == ""
        assert config.password == ""
        assert config.poll_interval == 30.0
        assert config.enabled is False

    def test_email_config_enabled_flag(self):
        config = EmailConfig(
            imap_host="imap.example.com",
            username="u",
            password="p",
            enabled=True,
        )
        assert config.enabled is True


# ---------------------------------------------------------------------------
# _poll_inbox tests
# ---------------------------------------------------------------------------


class TestPollInboxParsesMessages:
    async def test_poll_inbox_parses_messages(self):
        """_poll_inbox should return normalised InboundMessage objects."""
        raw_bytes = _make_raw_email_bytes(
            from_addr="alice@example.com",
            subject="Hello",
            message_id="<parse-test@example.com>",
        )
        mock_imap = _make_mock_imap(unseen_nums=b"1", raw_bytes=raw_bytes)

        config = _make_config()
        poller = EmailPoller(config=config)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            messages = await poller._poll_inbox()

        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, InboundMessage)
        assert msg.sender == "alice@example.com"
        assert msg.subject == "Hello"
        assert msg.message_id == "parse-test@example.com"
        assert msg.channel == "email"

    async def test_poll_inbox_no_unseen_returns_empty(self):
        """When no unseen messages, _poll_inbox returns an empty list."""
        mock_imap = _make_mock_imap(unseen_nums=b"")
        config = _make_config()
        poller = EmailPoller(config=config)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            messages = await poller._poll_inbox()

        assert messages == []
        mock_imap.fetch.assert_not_called()

    async def test_poll_inbox_marks_messages_seen(self):
        """After processing, messages should be marked \\Seen."""
        mock_imap = _make_mock_imap(unseen_nums=b"1 2")
        config = _make_config()
        poller = EmailPoller(config=config)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_inbox()

        assert mock_imap.store.call_count == 2

    async def test_poll_inbox_publishes_to_broker(self):
        """_poll_inbox should publish normalised messages to the broker."""
        raw_bytes = _make_raw_email_bytes(message_id="<broker-test@example.com>")
        mock_imap = _make_mock_imap(unseen_nums=b"1", raw_bytes=raw_bytes)
        broker = MockBroker()
        config = _make_config()
        poller = EmailPoller(config=config, broker=broker)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            await poller._poll_inbox()

        assert len(broker.published) == 1
        from graphclaw.infra.broker import INBOUND_MESSAGES

        queue, _ = broker.published[0]
        assert queue == INBOUND_MESSAGES


# ---------------------------------------------------------------------------
# send_email tests
# ---------------------------------------------------------------------------


class TestSendCallsAiosmtplib:
    async def test_send_calls_aiosmtplib(self):
        """send_email should call aiosmtplib.send with correct parameters."""
        config = _make_config(smtp_host="smtp.example.com", smtp_port=587)
        _NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        message = OutboundMessage(
            message_id="out-001",
            channel="email",
            recipient="bob@example.com",
            subject="Test Subject",
            body="Test body",
            created_at=_NOW,
        )

        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email(message, config)

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["hostname"] == "smtp.example.com"
        assert kwargs["port"] == 587
        assert kwargs["username"] == "user@example.com"
        assert kwargs["password"] == "secret"

    async def test_send_raises_for_non_email_channel(self):
        """send_email should raise ValueError for non-email channels."""
        config = _make_config()
        _NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        message = OutboundMessage(
            message_id="out-002",
            channel="api",
            recipient="user-123",
            subject="Notification",
            body="Body",
            created_at=_NOW,
        )
        with pytest.raises(ValueError, match="send_email only handles channel='email'"):
            await send_email(message, config)

    async def test_send_sets_in_reply_to_header(self):
        """When in_reply_to is set, the email headers should reflect it."""
        config = _make_config()
        _NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        message = OutboundMessage(
            message_id="out-003",
            channel="email",
            recipient="carol@example.com",
            subject="Re: Original",
            body="Reply text",
            created_at=_NOW,
            in_reply_to="<original-id@example.com>",
        )

        captured_msg = None

        async def capture_send(msg, **kwargs):
            nonlocal captured_msg
            captured_msg = msg

        with patch("aiosmtplib.send", side_effect=capture_send):
            await send_email(message, config)

        assert captured_msg is not None
        assert "In-Reply-To" in captured_msg
        assert "original-id@example.com" in captured_msg["In-Reply-To"]


# ---------------------------------------------------------------------------
# Poller stop flag tests
# ---------------------------------------------------------------------------


class TestPollerStopFlag:
    async def test_poller_stop_flag(self):
        """stop() should set _running to False."""
        config = _make_config(poll_interval=0)
        poller = EmailPoller(config=config)
        poller._running = True

        await poller.stop()

        assert poller._running is False

    async def test_poller_start_and_stop(self):
        """start() should loop and exit when stop() is called."""
        config = _make_config(poll_interval=0)
        poller = EmailPoller(config=config)

        call_count = 0

        async def mock_poll_once():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                poller._running = False

        # start() calls _poll_once(), not _poll_inbox()
        poller._poll_once = mock_poll_once  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await poller.start()

        assert not poller._running
        assert call_count >= 2
