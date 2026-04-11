"""tests.test_gateway.test_whatsapp — Unit tests for WhatsApp channel adapter.

Tests the normalizer, config, sender, and adapter independently using mocks
so no real Meta API calls are made.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from graphclaw.gateway.channels.whatsapp.config import WhatsAppConfig
from graphclaw.gateway.channels.whatsapp.normalizer import (
    extract_whatsapp_attachments,
    normalize_whatsapp,
)
from graphclaw.gateway.schemas import InboundMessage

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestWhatsAppConfig:
    def test_from_env_all_set(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "EAAB...")
        monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", "mysecret")
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "mytoken")
        monkeypatch.setenv("WHATSAPP_API_VERSION", "v19.0")

        config = WhatsAppConfig.from_env()
        assert config is not None
        assert config.phone_number_id == "1234567890"
        assert config.api_version == "v19.0"

    def test_from_env_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
        monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("WHATSAPP_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)

        assert WhatsAppConfig.from_env() is None

    def test_api_base_url(self):
        config = WhatsAppConfig(
            phone_number_id="123",
            access_token="tok",
            webhook_secret="sec",
            verify_token="ver",
            api_version="v20.0",
        )
        assert config.api_base_url == "https://graph.facebook.com/v20.0/123"


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


_SAMPLE_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "value": {
                        "messages": [
                            {
                                "id": "wamid.abc123",
                                "from": "15551234567",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "Hello world"},
                            }
                        ],
                        "contacts": [
                            {
                                "profile": {"name": "Alice"},
                                "wa_id": "15551234567",
                            }
                        ],
                    }
                }
            ]
        }
    ]
}


class TestNormalizeWhatsApp:
    def test_extracts_text_message(self):
        msgs = normalize_whatsapp(_SAMPLE_PAYLOAD)
        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "whatsapp"
        assert msg.sender == "15551234567"
        assert msg.body == "Hello world"
        assert "Alice" in msg.subject

    def test_sets_session_id(self):
        msgs = normalize_whatsapp(_SAMPLE_PAYLOAD)
        assert msgs[0].session_id.startswith("SES-")

    def test_sets_received_at_from_timestamp(self):
        msgs = normalize_whatsapp(_SAMPLE_PAYLOAD)
        assert msgs[0].received_at == datetime.fromtimestamp(1700000000, tz=timezone.utc)

    def test_skips_non_text_messages(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.img1",
                                        "from": "15551234567",
                                        "timestamp": "1700000000",
                                        "type": "image",
                                        "image": {"id": "media123", "mime_type": "image/jpeg"},
                                    }
                                ],
                                "contacts": [],
                            }
                        }
                    ]
                }
            ]
        }
        msgs = normalize_whatsapp(payload)
        assert msgs == []

    def test_empty_payload_returns_empty(self):
        assert normalize_whatsapp({}) == []

    def test_malformed_payload_returns_empty(self):
        assert normalize_whatsapp({"entry": "not-a-list"}) == []

    def test_uses_uuid_when_no_timestamp(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.xyz",
                                        "from": "123",
                                        "timestamp": "0",
                                        "type": "text",
                                        "text": {"body": "hi"},
                                    }
                                ],
                                "contacts": [],
                            }
                        }
                    ]
                }
            ]
        }
        msgs = normalize_whatsapp(payload)
        assert len(msgs) == 1
        # received_at should be close to now
        delta = abs((msgs[0].received_at - datetime.now(timezone.utc)).total_seconds())
        assert delta < 5

    def test_raw_headers_populated(self):
        msgs = normalize_whatsapp(_SAMPLE_PAYLOAD)
        assert msgs[0].raw_headers.get("wa_sender_name") == "Alice"
        assert msgs[0].raw_headers.get("wa_message_type") == "text"


class TestExtractWhatsAppAttachments:
    def test_extracts_image(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.img1",
                                        "from": "15551234567",
                                        "type": "image",
                                        "image": {
                                            "id": "media123",
                                            "mime_type": "image/jpeg",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        atts = extract_whatsapp_attachments(payload)
        assert len(atts) == 1
        assert atts[0]["type"] == "image"
        assert atts[0]["media_id"] == "media123"
        assert atts[0]["sender"] == "15551234567"

    def test_skips_text_messages(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.txt1",
                                        "from": "123",
                                        "type": "text",
                                        "text": {"body": "hi"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        assert extract_whatsapp_attachments(payload) == []

    def test_empty_payload(self):
        assert extract_whatsapp_attachments({}) == []


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


class TestWhatsAppChannelAdapter:
    @pytest.fixture
    def _env_vars(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", "secret")
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verifytoken")

    async def test_start_sets_config(self, _env_vars):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        broker = AsyncMock()
        adapter = WhatsAppChannelAdapter()
        await adapter.start(broker)
        assert adapter._config is not None
        assert adapter._sender is not None

    async def test_start_skips_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
        monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("WHATSAPP_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)

        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        broker = AsyncMock()
        adapter = WhatsAppChannelAdapter()
        await adapter.start(broker)
        assert adapter._config is None

    async def test_verify_webhook_token(self, _env_vars):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        broker = AsyncMock()
        adapter = WhatsAppChannelAdapter()
        await adapter.start(broker)
        assert adapter.verify_webhook_token("verifytoken") is True
        assert adapter.verify_webhook_token("wrong") is False

    async def test_verify_signature(self, _env_vars):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        broker = AsyncMock()
        adapter = WhatsAppChannelAdapter()
        await adapter.start(broker)

        payload = b'{"test": "data"}'
        expected_sig = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
        assert adapter.verify_signature(payload, f"sha256={expected_sig}") is True
        assert adapter.verify_signature(payload, "sha256=wrongsig") is False
        assert adapter.verify_signature(payload, "badsig") is False

    async def test_handle_webhook_publishes_messages(self, _env_vars):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        broker = AsyncMock()
        adapter = WhatsAppChannelAdapter()
        await adapter.start(broker)

        count = await adapter.handle_webhook(_SAMPLE_PAYLOAD)
        assert count == 1
        broker.publish.assert_called_once()

    async def test_handle_webhook_no_broker(self, _env_vars):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        adapter = WhatsAppChannelAdapter()
        # _broker is None (never started)
        count = await adapter.handle_webhook(_SAMPLE_PAYLOAD)
        assert count == 0

    def test_channel_name(self):
        from graphclaw.gateway.channels.whatsapp.adapter import WhatsAppChannelAdapter

        assert WhatsAppChannelAdapter().channel_name == "whatsapp"
