"""graphclaw.gateway.channels.telegram.normalizer — Telegram Update → InboundMessage.

Translates Telegram Bot API Update objects (JSON dicts) into the
channel-agnostic ``InboundMessage`` schema used throughout the gateway.

Telegram Update structure (simplified):
    {
      "update_id": 123456789,
      "message": {
        "message_id": 42,
        "from": {
          "id": 987654321,
          "first_name": "Alice",
          "last_name": "Smith",
          "username": "alicesmith"
        },
        "chat": {"id": 987654321, "type": "private"},
        "date": 1700000000,
        "text": "Hello world"
      }
    }

Only ``message`` and ``edited_message`` with a ``text`` field are handled here.
Photo/document/audio messages are extracted by ``extract_telegram_attachments()``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from graphclaw.gateway.schemas import InboundMessage


def _sender_display(from_obj: dict[str, Any]) -> str:
    """Build a human-readable name from a Telegram ``from`` object."""
    first = from_obj.get("first_name", "")
    last = from_obj.get("last_name", "")
    username = from_obj.get("username", "")
    full = f"{first} {last}".strip()
    if username:
        return f"{full} (@{username})" if full else f"@{username}"
    return full or str(from_obj.get("id", "unknown"))


def normalize_telegram(update: dict[str, Any]) -> list[InboundMessage]:
    """Extract text messages from a single Telegram Update object.

    Returns a list (0 or 1 items) for consistency with the WhatsApp normalizer.
    Non-text updates (photo, document, audio, etc.) return an empty list.
    """
    messages: list[InboundMessage] = []

    try:
        # Support both new messages and edited messages
        msg = update.get("message") or update.get("edited_message")
        if msg is None:
            return messages

        text = msg.get("text", "")
        if not text:
            return messages  # Non-text handled by attachment extractor

        from_obj = msg.get("from", {})
        sender_id = str(from_obj.get("id", ""))
        sender_name = _sender_display(from_obj)

        ts = int(msg.get("date", 0))
        received_at = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC)

        chat_id = str(msg.get("chat", {}).get("id", sender_id))
        msg_id = f"tg-{update.get('update_id', uuid.uuid4().hex)}"

        messages.append(
            InboundMessage(
                message_id=msg_id,
                channel="telegram",
                sender=sender_id,
                subject=f"Telegram from {sender_name}",
                body=text,
                received_at=received_at,
                raw_headers={
                    "tg_sender_name": sender_name,
                    "tg_chat_id": chat_id,
                    "tg_update_id": str(update.get("update_id", "")),
                },
                session_id=f"SES-{uuid.uuid4()}",
            )
        )
    except (KeyError, TypeError, ValueError):
        pass  # Malformed update — return empty list, caller logs warning

    return messages


def extract_telegram_attachments(update: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract non-text content from a Telegram Update for the attachment handler.

    Returns a list of raw attachment dicts with keys:
        - ``msg_id``: Telegram message ID (prefixed with "tg-{update_id}")
        - ``sender``: Telegram user ID string
        - ``type``: "photo" | "document" | "audio" | "video" | "voice" | "sticker"
        - ``file_id``: Telegram file_id (used to download via getFile)
        - ``mime_type``: MIME type (for document/audio/video)
        - ``filename``: filename (for documents)
    """
    attachments = []
    try:
        msg = update.get("message") or update.get("edited_message")
        if msg is None:
            return attachments

        sender_id = str(msg.get("from", {}).get("id", ""))
        msg_id = f"tg-{update.get('update_id', '')}"

        # Photo — array of PhotoSize; take the largest (last)
        if "photo" in msg:
            photos = msg["photo"]
            if photos:
                largest = photos[-1]
                attachments.append(
                    {
                        "msg_id": msg_id,
                        "sender": sender_id,
                        "type": "photo",
                        "file_id": largest.get("file_id", ""),
                        "mime_type": "image/jpeg",
                        "filename": "",
                    }
                )
        # Single-file types
        for media_type in ("document", "audio", "video", "voice", "sticker"):
            media = msg.get(media_type)
            if media:
                attachments.append(
                    {
                        "msg_id": msg_id,
                        "sender": sender_id,
                        "type": media_type,
                        "file_id": media.get("file_id", ""),
                        "mime_type": media.get("mime_type", ""),
                        "filename": media.get("file_name", ""),
                    }
                )
    except (KeyError, TypeError):
        pass
    return attachments
