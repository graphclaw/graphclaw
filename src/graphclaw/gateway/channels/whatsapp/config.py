"""graphclaw.gateway.channels.whatsapp.config — WhatsApp channel configuration.

Reads WhatsApp Cloud API credentials from environment variables.
All variables are optional at import time — missing values cause the
adapter to skip startup with a warning rather than crashing.

Environment Variables
---------------------
WHATSAPP_PHONE_NUMBER_ID   WhatsApp Cloud API phone number ID.
WHATSAPP_ACCESS_TOKEN      Meta permanent or system-user access token.
WHATSAPP_WEBHOOK_SECRET    App secret used to verify webhook signatures.
WHATSAPP_VERIFY_TOKEN      Verification token for webhook registration.
WHATSAPP_API_VERSION       Graph API version (default: v20.0).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WhatsAppConfig:
    """Validated WhatsApp Cloud API configuration."""

    phone_number_id: str
    access_token: str
    webhook_secret: str
    verify_token: str
    api_version: str = "v20.0"

    @property
    def api_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"

    @classmethod
    def from_env(cls) -> WhatsAppConfig | None:
        """Build from environment variables; return None if incomplete."""
        phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        secret = os.environ.get("WHATSAPP_WEBHOOK_SECRET", "")
        verify = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")

        if not all([phone_id, token, secret, verify]):
            return None

        return cls(
            phone_number_id=phone_id,
            access_token=token,
            webhook_secret=secret,
            verify_token=verify,
            api_version=os.environ.get("WHATSAPP_API_VERSION", "v20.0"),
        )
