# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channels.email.normalizer — Email-to-InboundMessage normalization.

Description
-----------
Provides ``normalize_email``, which converts a parsed ``email.message.EmailMessage``
object (as returned by ``email.message_from_bytes``) into a canonical
``InboundMessage`` DTO.  The function extracts the standard RFC 2822 headers,
decodes the best available plain-text body part, and generates a unique
``session_id`` for distributed tracing.

Design Patterns
---------------
- Pure Function: ``normalize_email`` is a stateless transformation with no side
  effects, making it trivially testable and composable.
- Adapter: Bridges the standard-library ``email.message.EmailMessage`` interface
  to the project's ``InboundMessage`` value object.

Public API
----------
- normalize_email: Convert ``email.message.EmailMessage`` to ``InboundMessage``.

Dependencies
------------
- graphclaw.gateway.schemas: InboundMessage.
- email.message: EmailMessage (stdlib).
- email.utils: parseaddr, parsedate_to_datetime (stdlib).
- uuid: uuid4 (stdlib).
- datetime: datetime, timezone (stdlib).
- logging: structured logging.

Notes
-----
Body extraction prefers ``text/plain`` parts. For multipart messages the first
``text/plain`` part is used; if no plain part exists the first ``text/html``
part is decoded as a fallback.  If neither is present the body is set to an
empty string.

``Message-ID`` is stripped of angle-brackets and whitespace before storage so
that it can be reliably used as a key.  Missing subjects are normalized to the
empty string rather than ``None``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import TYPE_CHECKING

from graphclaw.gateway.schemas import InboundMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _decode_payload(part: EmailMessage) -> str:
    """Decode the payload of a single email part to a string.

    Attempts to use the charset declared in the part's Content-Type header.
    Falls back to UTF-8 then latin-1 if the declared charset fails.
    """
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return str(payload or "")
    charset = part.get_content_charset() or "utf-8"
    for encoding in (charset, "utf-8", "latin-1"):
        try:
            return payload.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("latin-1", errors="replace")


def _extract_body(msg: EmailMessage) -> str:
    """Extract the best available plain-text body from an email message.

    For multipart messages, iterates parts and returns the first ``text/plain``
    part.  Falls back to the first ``text/html`` part if no plain part is found.
    For non-multipart messages, returns the decoded payload directly if the
    content type is ``text/plain`` or ``text/html``.
    """
    if msg.is_multipart():
        plain_part: EmailMessage | None = None
        html_part: EmailMessage | None = None
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" and plain_part is None:
                plain_part = part  # type: ignore[assignment]
            elif content_type == "text/html" and html_part is None:
                html_part = part  # type: ignore[assignment]
        chosen = plain_part or html_part
        if chosen is not None:
            return _decode_payload(chosen)
        return ""
    content_type = msg.get_content_type()
    if content_type in ("text/plain", "text/html"):
        return _decode_payload(msg)
    return ""


def _extract_attachments(msg: EmailMessage) -> list[str]:
    """Return a list of attachment filenames found in the message."""
    filenames: list[str] = []
    if not msg.is_multipart():
        return filenames
    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            filename = part.get_filename()
            if filename:
                filenames.append(filename)
    return filenames


def _parse_received_at(msg: EmailMessage) -> datetime:
    """Parse the ``Date`` header into a timezone.utc-aware ``datetime``.

    Returns the current timezone.utc time if the header is missing or unparseable.
    """
    date_str = msg.get("Date")
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:  # noqa: BLE001
            logger.debug("Failed to parse Date header %r, using now()", date_str)
    return datetime.now(tz=timezone.utc)


def normalize_email(raw_email: EmailMessage) -> InboundMessage:
    """Convert a parsed ``email.message.EmailMessage`` into an ``InboundMessage``.

    Parameters
    ----------
    raw_email:
        A fully parsed email message object, typically produced by
        ``email.message_from_bytes(data, _class=EmailMessage)``.

    Returns
    -------
    InboundMessage:
        Normalized inbound message ready to be published to the broker queue.

    Notes
    -----
    The generated ``session_id`` uses the ``SES-{uuid4}`` format mandated by
    the project's distributed-tracing specification.
    """
    # --- Sender ---
    from_raw = raw_email.get("From", "")
    _display_name, sender = parseaddr(from_raw)

    # --- Subject ---
    subject = raw_email.get("Subject") or ""

    # --- Message-ID ---
    message_id_raw = raw_email.get("Message-ID", "")
    message_id = message_id_raw.strip().strip("<>")
    if not message_id:
        message_id = str(uuid.uuid4())

    # --- In-Reply-To ---
    in_reply_to_raw = raw_email.get("In-Reply-To")
    in_reply_to: str | None = None
    if in_reply_to_raw:
        in_reply_to = in_reply_to_raw.strip().strip("<>") or None

    # --- Date ---
    received_at = _parse_received_at(raw_email)

    # --- Raw headers (string values only) ---
    raw_headers: dict[str, str] = {}
    for key in raw_email.keys():
        value = raw_email.get(key)
        if value is not None:
            raw_headers[key] = str(value)

    # --- Body ---
    body = _extract_body(raw_email)

    # --- Attachments ---
    attachments = _extract_attachments(raw_email)

    # --- Session ID ---
    session_id = f"SES-{uuid.uuid4()}"

    return InboundMessage(
        message_id=message_id,
        channel="email",
        sender=sender,
        subject=subject,
        body=body,
        received_at=received_at,
        raw_headers=raw_headers,
        attachments=attachments,
        session_id=session_id,
        in_reply_to=in_reply_to,
    )
