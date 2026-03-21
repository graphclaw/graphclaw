"""graphclaw.gateway.channels.whatsapp.sender — WhatsApp outbound message delivery.

Sends text messages via the WhatsApp Cloud API using ``httpx`` async HTTP.

Environment Variables (via WhatsAppConfig)
------------------------------------------
WHATSAPP_PHONE_NUMBER_ID   Phone number to send from.
WHATSAPP_ACCESS_TOKEN      Meta access token.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.gateway.channels.whatsapp.config import WhatsAppConfig

logger = logging.getLogger(__name__)


class WhatsAppSender:
    """Delivers outbound messages via the WhatsApp Cloud API."""

    def __init__(self, config: WhatsAppConfig) -> None:
        self._config = config

    async def send(self, recipient: str, body: str) -> None:
        """Send a text message to *recipient* (E.164 phone number, e.g. '15551234567').

        Args:
            recipient: Destination phone number without '+' prefix.
            body: Plain text message content (max 4096 chars).

        Raises:
            RuntimeError: If the API call fails or httpx is not installed.
        """
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for the WhatsApp channel. "
                "Install it with: pip install 'httpx>=0.27.0'"
            ) from exc

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        headers = {
            "Authorization": f"Bearer {self._config.access_token}",
            "Content-Type": "application/json",
        }
        url = f"{self._config.api_base_url}/messages"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"WhatsApp API send failed: HTTP {response.status_code} — {response.text[:200]}"
            )
        logger.info("WhatsApp message sent to %s", recipient)
