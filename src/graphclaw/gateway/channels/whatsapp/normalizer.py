"""graphclaw.gateway.channels.whatsapp.normalizer — WhatsApp → InboundMessage.

Translates the WhatsApp Cloud API webhook payload (a nested JSON dict) into
the channel-agnostic ``InboundMessage`` schema used throughout the gateway.

WhatsApp payload structure (simplified):
    {
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{
              "id": "wamid.xxx",
              "from": "15551234567",
              "timestamp": "1700000000",
              "type": "text",
              "text": {"body": "Hello world"}
            }],
            "contacts": [{"profile": {"name": "Alice"}, "wa_id": "15551234567"}]
          }
        }]
      }]
    }
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from graphclaw.gateway.schemas import InboundMessage


def normalize_whatsapp(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract all text messages from a WhatsApp webhook payload.

    Returns a list because a single webhook delivery can contain multiple
    messages (though this is rare in practice).

    Skips non-text message types (image, audio, document, etc.) — those
    are handled by the attachment handler.
    """
    messages: list[InboundMessage] = []

    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {
                    c["wa_id"]: c.get("profile", {}).get("name", "")
                    for c in value.get("contacts", [])
                }
                for msg in value.get("messages", []):
                    msg_type = msg.get("type", "")
                    if msg_type != "text":
                        continue  # Non-text handled separately

                    sender_id = msg.get("from", "")
                    sender_name = contacts.get(sender_id, "")
                    body = msg.get("text", {}).get("body", "")
                    msg_id = msg.get("id", str(uuid.uuid4()))
                    ts = int(msg.get("timestamp", 0))
                    received_at = (
                        datetime.fromtimestamp(ts, tz=timezone.utc)
                        if ts
                        else datetime.now(timezone.utc)
                    )

                    messages.append(
                        InboundMessage(
                            message_id=msg_id,
                            channel="whatsapp",
                            sender=sender_id,
                            subject=f"WhatsApp from {sender_name or sender_id}",
                            body=body,
                            received_at=received_at,
                            raw_headers={
                                "wa_sender_name": sender_name,
                                "wa_message_type": msg_type,
                            },
                            session_id=f"SES-{uuid.uuid4()}",
                        )
                    )
    except (AttributeError, KeyError, TypeError, ValueError):
        pass  # Malformed payload — return empty list, caller logs warning

    return messages


def extract_whatsapp_attachments(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract non-text message objects (images, audio, documents) for the attachment handler.

    Returns a list of raw attachment dicts with keys:
        - ``msg_id``: WhatsApp message ID
        - ``sender``: sender phone number
        - ``type``: "image" | "audio" | "document" | "video" | "sticker"
        - ``media_id``: WhatsApp media ID (used to download via Graph API)
        - ``mime_type``: MIME type string
        - ``filename``: filename (for documents)
    """
    attachments = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    msg_type = msg.get("type", "text")
                    if msg_type == "text":
                        continue
                    media_obj = msg.get(msg_type, {})
                    attachments.append(
                        {
                            "msg_id": msg.get("id", ""),
                            "sender": msg.get("from", ""),
                            "type": msg_type,
                            "media_id": media_obj.get("id", ""),
                            "mime_type": media_obj.get("mime_type", ""),
                            "filename": media_obj.get("filename", ""),
                        }
                    )
    except (KeyError, TypeError):
        pass
    return attachments
