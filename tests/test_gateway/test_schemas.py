"""Tests for graphclaw.gateway.schemas — InboundMessage and OutboundMessage models."""

from __future__ import annotations

from datetime import UTC, datetime

from graphclaw.gateway.schemas import InboundMessage, OutboundMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_inbound(**overrides) -> InboundMessage:
    defaults = dict(
        message_id="msg-001",
        channel="email",
        sender="alice@example.com",
        subject="Hello",
        body="Test body",
        received_at=_NOW,
    )
    defaults.update(overrides)
    return InboundMessage(**defaults)


def _make_outbound(**overrides) -> OutboundMessage:
    defaults = dict(
        message_id="out-001",
        channel="email",
        recipient="bob@example.com",
        subject="Re: Hello",
        body="Reply body",
        created_at=_NOW,
    )
    defaults.update(overrides)
    return OutboundMessage(**defaults)


# ---------------------------------------------------------------------------
# InboundMessage tests
# ---------------------------------------------------------------------------


class TestInboundMessageCreation:
    def test_inbound_message_creation(self):
        msg = _make_inbound()
        assert msg.message_id == "msg-001"
        assert msg.channel == "email"
        assert msg.sender == "alice@example.com"
        assert msg.subject == "Hello"
        assert msg.body == "Test body"
        assert msg.received_at == _NOW

    def test_inbound_message_defaults(self):
        msg = _make_inbound()
        assert msg.raw_headers == {}
        assert msg.attachments == []
        assert msg.session_id == ""
        assert msg.in_reply_to is None

    def test_inbound_message_with_optional_fields(self):
        msg = _make_inbound(
            raw_headers={"X-Mailer": "Thunderbird"},
            attachments=["report.pdf"],
            session_id="SES-abc123",
            in_reply_to="<original@example.com>",
        )
        assert msg.raw_headers == {"X-Mailer": "Thunderbird"}
        assert msg.attachments == ["report.pdf"]
        assert msg.session_id == "SES-abc123"
        assert msg.in_reply_to == "<original@example.com>"


class TestInboundMessageSerializationRoundtrip:
    def test_inbound_message_serialization_roundtrip(self):
        original = _make_inbound(
            session_id="SES-xyz",
            in_reply_to="prev-msg-id",
            raw_headers={"From": "alice@example.com"},
            attachments=["a.txt", "b.pdf"],
        )
        json_str = original.model_dump_json()
        restored = InboundMessage.model_validate_json(json_str)

        assert restored.message_id == original.message_id
        assert restored.channel == original.channel
        assert restored.sender == original.sender
        assert restored.subject == original.subject
        assert restored.body == original.body
        assert restored.received_at == original.received_at
        assert restored.raw_headers == original.raw_headers
        assert restored.attachments == original.attachments
        assert restored.session_id == original.session_id
        assert restored.in_reply_to == original.in_reply_to

    def test_roundtrip_preserves_timezone(self):
        msg = _make_inbound(received_at=_NOW)
        restored = InboundMessage.model_validate_json(msg.model_dump_json())
        assert restored.received_at.tzinfo is not None


# ---------------------------------------------------------------------------
# OutboundMessage tests
# ---------------------------------------------------------------------------


class TestOutboundMessageCreation:
    def test_outbound_message_creation(self):
        msg = _make_outbound()
        assert msg.message_id == "out-001"
        assert msg.channel == "email"
        assert msg.recipient == "bob@example.com"
        assert msg.subject == "Re: Hello"
        assert msg.body == "Reply body"
        assert msg.created_at == _NOW

    def test_outbound_message_defaults(self):
        msg = _make_outbound()
        assert msg.in_reply_to is None
        assert msg.session_id == ""

    def test_outbound_message_with_optional_fields(self):
        msg = _make_outbound(
            in_reply_to="msg-001",
            session_id="SES-abc",
        )
        assert msg.in_reply_to == "msg-001"
        assert msg.session_id == "SES-abc"

    def test_outbound_message_serialization_roundtrip(self):
        original = _make_outbound(in_reply_to="parent-id", session_id="SES-qrs")
        restored = OutboundMessage.model_validate_json(original.model_dump_json())
        assert restored.message_id == original.message_id
        assert restored.recipient == original.recipient
        assert restored.in_reply_to == original.in_reply_to
        assert restored.session_id == original.session_id
