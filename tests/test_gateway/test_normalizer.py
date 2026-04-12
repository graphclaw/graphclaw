"""Tests for graphclaw.gateway.channels.email.normalizer — normalize_email function."""

from __future__ import annotations

from email.message import EmailMessage

from graphclaw.gateway.channels.email.normalizer import normalize_email
from graphclaw.gateway.schemas import InboundMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_email(
    from_addr: str = "alice@example.com",
    subject: str = "Test Subject",
    body: str = "Plain text body",
    message_id: str = "<abc123@mail.example.com>",
    in_reply_to: str | None = None,
    date: str = "Thu, 01 Jun 2024 12:00:00 +0000",
) -> EmailMessage:
    """Build a minimal plain-text EmailMessage for testing."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = date
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


def _make_multipart_email() -> EmailMessage:
    """Build a multipart/alternative email with plain-text and HTML parts."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Multipart Message"
    msg["Message-ID"] = "<multi@mail.example.com>"
    msg["Date"] = "Thu, 01 Jun 2024 12:00:00 +0000"
    msg.set_content("This is the plain text part.")
    msg.add_alternative("<html><body>HTML part</body></html>", subtype="html")
    return msg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormalizePlainTextEmail:
    def test_normalize_plain_text_email(self):
        raw = _make_email()
        result = normalize_email(raw)

        assert isinstance(result, InboundMessage)
        assert result.channel == "email"
        assert result.sender == "alice@example.com"
        assert result.subject == "Test Subject"
        assert "Plain text body" in result.body

    def test_message_id_stripped_of_angle_brackets(self):
        raw = _make_email(message_id="<stripped@example.com>")
        result = normalize_email(raw)
        assert result.message_id == "stripped@example.com"

    def test_received_at_is_datetime(self):
        raw = _make_email()
        result = normalize_email(raw)
        from datetime import datetime

        assert isinstance(result.received_at, datetime)
        assert result.received_at.tzinfo is not None


class TestNormalizeMultipartEmail:
    def test_normalize_multipart_email(self):
        raw = _make_multipart_email()
        result = normalize_email(raw)

        assert result.sender == "sender@example.com"
        assert result.subject == "Multipart Message"
        # Plain-text part should be preferred
        assert "plain text part" in result.body.lower()

    def test_multipart_extracts_plain_over_html(self):
        raw = _make_multipart_email()
        result = normalize_email(raw)
        # Should NOT contain raw HTML tags
        assert "<html>" not in result.body


class TestNormalizeExtractsHeaders:
    def test_normalize_extracts_headers(self):
        raw = _make_email(
            from_addr="bob@example.com",
            subject="Header Test",
        )
        result = normalize_email(raw)

        assert "From" in result.raw_headers or "from" in {k.lower() for k in result.raw_headers}
        # Verify subject was captured
        assert result.subject == "Header Test"

    def test_raw_headers_are_strings(self):
        raw = _make_email()
        result = normalize_email(raw)
        for key, value in result.raw_headers.items():
            assert isinstance(key, str)
            assert isinstance(value, str)


class TestNormalizeGeneratesSessionId:
    def test_normalize_generates_session_id(self):
        raw = _make_email()
        result = normalize_email(raw)
        assert result.session_id.startswith("SES-")
        # Should be SES- followed by a uuid4
        parts = result.session_id.split("-", 1)
        assert parts[0] == "SES"
        assert len(parts[1]) > 0

    def test_each_call_generates_unique_session_id(self):
        raw = _make_email()
        r1 = normalize_email(raw)
        r2 = normalize_email(raw)
        assert r1.session_id != r2.session_id


class TestNormalizeHandlesMissingSubject:
    def test_normalize_handles_missing_subject(self):
        msg = EmailMessage()
        msg["From"] = "nosubject@example.com"
        msg["Message-ID"] = "<nosubj@example.com>"
        msg["Date"] = "Thu, 01 Jun 2024 12:00:00 +0000"
        msg.set_content("Body without subject")

        result = normalize_email(msg)
        # Should not raise; subject should be empty string
        assert result.subject == ""
        assert result.sender == "nosubject@example.com"

    def test_normalize_handles_missing_message_id(self):
        msg = EmailMessage()
        msg["From"] = "user@example.com"
        msg["Subject"] = "No ID"
        msg["Date"] = "Thu, 01 Jun 2024 12:00:00 +0000"
        msg.set_content("Body")

        result = normalize_email(msg)
        # Should generate a fallback message_id
        assert result.message_id != ""

    def test_normalize_in_reply_to_stripped(self):
        raw = _make_email(in_reply_to="<parent-id@example.com>")
        result = normalize_email(raw)
        assert result.in_reply_to == "parent-id@example.com"

    def test_normalize_no_in_reply_to(self):
        raw = _make_email()
        result = normalize_email(raw)
        assert result.in_reply_to is None
